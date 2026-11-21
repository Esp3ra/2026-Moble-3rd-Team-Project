using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

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
    private static readonly HttpClient Client = new() { Timeout = TimeSpan.FromSeconds(8) };
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private sealed record ApiError([property: JsonPropertyName("error")] string? Error);

    public async Task<AdminSession> LoginAsync(Uri server, string loginId, string password)
    {
        using var response = await Client.PostAsJsonAsync(new Uri(server, "/api/auth/login"),
            new { login_id = loginId, password });
        if (!response.IsSuccessStatusCode)
        {
            var error = await response.Content.ReadFromJsonAsync<ApiError>(JsonOptions);
            throw new HttpRequestException(error?.Error ?? "로그인에 실패했습니다.", null, response.StatusCode);
        }
        var session = await response.Content.ReadFromJsonAsync<AdminSession>(JsonOptions);
        if (session is null || string.IsNullOrEmpty(session.Token) || session.Account is null ||
            session.Account.RoleCode is not ("ADMIN" or "OPERATOR"))
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
