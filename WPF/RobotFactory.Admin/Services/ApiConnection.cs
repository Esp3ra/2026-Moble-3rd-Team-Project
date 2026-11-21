using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;

namespace RobotFactory.Admin.Services;

internal static class ApiConnection
{
    internal const string AddressHint = "HTTPS 기본 주소를 입력하세요. 같은 PC의 서버는 HTTP를 사용할 수 있습니다.";

    internal static bool TryParseAddress(string address, out Uri server)
    {
        if (Uri.TryCreate(address, UriKind.Absolute, out var candidate) &&
            (candidate.Scheme == Uri.UriSchemeHttps ||
             (candidate.Scheme == Uri.UriSchemeHttp && candidate.IsLoopback)) &&
            candidate.UserInfo.Length == 0 && candidate.AbsolutePath == "/" &&
            candidate.Query.Length == 0 && candidate.Fragment.Length == 0)
        {
            server = candidate;
            return true;
        }
        server = null!;
        return false;
    }

    internal static HttpClient CreateClient(TimeSpan timeout) =>
        new(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false }) { Timeout = timeout };

    internal static async Task<HttpRequestException> CreateErrorAsync(
        HttpResponseMessage response, string fallback, CancellationToken cancellationToken)
    {
        string? reason = null;
        if (response.Content.Headers.ContentType?.MediaType is "application/json" or "application/problem+json")
        {
            try
            {
                var content = await response.Content.ReadFromJsonAsync<ApiError>(cancellationToken);
                reason = content?.Error;
            }
            catch (JsonException) { }
            catch (NotSupportedException) { }
        }
        var message = string.IsNullOrWhiteSpace(reason) ? fallback : reason;
        return new HttpRequestException(message, null, response.StatusCode);
    }

    private sealed record ApiError(string? Error);
}
