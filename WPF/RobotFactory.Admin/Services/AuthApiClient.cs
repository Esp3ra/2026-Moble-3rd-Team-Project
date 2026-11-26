using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace RobotFactory.Admin.Services;

public sealed record AdminAccount(string AccountId,string Role,string DisplayName)
{
    public string DisplayRole => Role == "SUPER_ADMIN" ? "최상위 관리자" : "1차 관리자";
}
public sealed record AdminSession(string Token,AdminAccount Account);

public sealed class AuthApiClient
{
    private static readonly HttpClient Client = ApiConnection.CreateClient(TimeSpan.FromSeconds(8));
    private static readonly HttpClient LocalClient = new(new HttpClientHandler
    {
        AllowAutoRedirect = false, UseCookies = false, UseProxy = false
    }) { Timeout = TimeSpan.FromSeconds(8) };
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    public Task<AdminSession> LoginAsync(Uri server, string loginId, string password) =>
        SendLoginAsync(server, loginId, password, false);

    internal async Task<AdminSession> LoginFromScreenAsync(Uri server, string loginId, string password, bool conveyorTrial)
    {
        if (conveyorTrial && (server.Host != "127.0.0.1" || server.Scheme != "http"))
            throw new HttpRequestException("컨베이어 시험은 메인 PC의 http://127.0.0.1 주소를 사용하세요.");
        var client = server.IsLoopback ? LocalClient : Client;
        using var catalog = await client.GetAsync(new Uri(server, "/api/v1/catalog"), HttpCompletionOption.ResponseHeadersRead);
        catalog.EnsureSuccessStatusCode();
        bool isTrial = catalog.Headers.Contains("X-Mobot-Test-Run");
        if (conveyorTrial != isTrial)
            throw new HttpRequestException(isTrial ? "시험 서버에 연결했습니다. 컨베이어 시험 모드를 선택하세요." :
                "컨베이어 시험 서버에 연결하세요.");
        if (conveyorTrial && (!HeaderIs(catalog, "X-Mobot-Test-Flow", "ir1-cam1-v1") ||
                              !HeaderIs(catalog, "X-Mobot-Test-Accounts", "1")))
            throw new HttpRequestException("기존 관리자 계정에 연결된 컨베이어 시험 서버를 실행하세요.");
        return await SendLoginAsync(server, loginId, password, conveyorTrial);
    }

    private static bool HeaderIs(HttpResponseMessage response, string name, string expected) =>
        response.Headers.TryGetValues(name, out var values) && values.SequenceEqual([expected]);

    private static async Task<AdminSession> SendLoginAsync(Uri server, string loginId, string password, bool conveyorTrial)
    {
        var path = conveyorTrial ? "/api/v1/station/auth/login" : "/api/v1/auth/login";
        var client = server.IsLoopback ? LocalClient : Client;
        using var response = await client.PostAsJsonAsync(new Uri(server, path),
            new { login_id = loginId, password });
        if (!response.IsSuccessStatusCode)
        {
            throw await ApiConnection.CreateErrorAsync(response, $"로그인 실패 (HTTP {(int)response.StatusCode})", CancellationToken.None);
        }
        var session = await response.Content.ReadFromJsonAsync<AdminSession>(JsonOptions);
        if (session is null || string.IsNullOrWhiteSpace(session.Token) || session.Token.Any(character => char.IsWhiteSpace(character) || char.IsControl(character)) || session.Account is null ||
            session.Account.Role is not ("ADMIN" or "SUPER_ADMIN") || string.IsNullOrEmpty(session.Account.AccountId) || string.IsNullOrWhiteSpace(session.Account.DisplayName))
            throw new JsonException("로그인 응답을 확인할 수 없습니다.");
        return session;
    }

    public async Task LogoutAsync(Uri server, string token)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(server, "/api/v1/auth/logout"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await Client.SendAsync(request);
        if (response.StatusCode != System.Net.HttpStatusCode.Unauthorized) response.EnsureSuccessStatusCode();
    }
}
