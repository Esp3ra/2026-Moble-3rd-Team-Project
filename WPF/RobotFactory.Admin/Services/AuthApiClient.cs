using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace RobotFactory.Admin.Services;

public sealed record AdminAccount
{
    public int AccountId { get; init; }
    public string LoginId { get; init; } = "";
    public string Name { get; init; } = "";
    public string RoleCode { get; init; } = "";
    public string RoleName { get; init; } = "";
    public string DisplayRole => RoleCode == "ADMIN" ? "최상위 관리자" : "1차 관리자";
}

public sealed record AdminSession(string Token, long ExpiresAt, AdminAccount Account);

public sealed class AuthApiClient
{
    private static readonly HttpClient Client = ApiConnection.CreateClient(TimeSpan.FromSeconds(8));
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    public async Task<AdminSession> LoginAsync(Uri server, string loginId, string password)
    {
        using var response = await Client.PostAsJsonAsync(new Uri(server, "/api/auth/login"),
            new { login_id = loginId, password });
        if (!response.IsSuccessStatusCode)
            throw await ApiConnection.CreateErrorAsync(response, "로그인에 실패했습니다.", CancellationToken.None);
        var session = await response.Content.ReadFromJsonAsync<AdminSession>(JsonOptions);
        if (session is null || string.IsNullOrWhiteSpace(session.Token) || session.Token.Any(character => char.IsWhiteSpace(character) || char.IsControl(character)) || session.Account is null ||
            session.Account.RoleCode is not ("ADMIN" or "OPERATOR") || session.Account.AccountId <= 0 ||
            string.IsNullOrWhiteSpace(session.Account.Name) || string.IsNullOrWhiteSpace(session.Account.LoginId) ||
            session.ExpiresAt <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
            throw new JsonException("로그인 응답을 확인할 수 없습니다.");
        return session;
    }

    public async Task LogoutAsync(Uri server, string token)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(server, "/api/auth/logout"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await Client.SendAsync(request);
        if (response.StatusCode != System.Net.HttpStatusCode.Unauthorized) response.EnsureSuccessStatusCode();
    }
}
