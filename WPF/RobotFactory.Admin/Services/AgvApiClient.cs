using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace RobotFactory.Admin.Services;

public sealed record AgvDevice
{
    public string State { get; init; } = "ready";
    public int Position { get; init; }
    public int ServoAngle { get; init; }
    public bool ForkMoving { get; init; }
}
public sealed record AgvTask
{
    public string TaskId { get; init; } = "";
    public string JobId { get; init; } = "";
    public string PalletId { get; init; } = "";
    public string EquipmentRunId { get; init; } = "";
    public int Destination { get; init; }
    public string State { get; init; } = "";
    public int StepIndex { get; init; }
    public string Error { get; init; } = "";
    public string? FinishedAt { get; init; }
}
public sealed record AgvEligible
{
    public string JobId { get; init; } = "";
}
public sealed record AgvState
{
    public string Mode { get; init; } = "";
    public AgvDevice Device { get; init; } = new();
    public AgvTask? Task { get; init; }
    public bool IsOwner { get; init; }
    public bool CanStart { get; init; }
    public bool CanStop { get; init; }
    public List<AgvEligible> Eligible { get; init; } = [];
}
public sealed class AgvApiClient
{
    private static readonly HttpClient Client = ApiConnection.CreateClient(TimeSpan.FromSeconds(3));
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private sealed record ApiError([property: JsonPropertyName("error")] string? Error);
    public async Task<AgvState> RequestAsync(Uri server, string token, string action, object? payload, CancellationToken cancellation)
    {
        using var request = new HttpRequestMessage(payload is null ? HttpMethod.Get : HttpMethod.Post, new Uri(server, "/api/agv/" + action));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (payload is not null) request.Content = JsonContent.Create(payload);
        using var response = await Client.SendAsync(request, cancellation);
        if (!response.IsSuccessStatusCode)
        {
            string message = (await response.Content.ReadFromJsonAsync<ApiError>(Options, cancellation))?.Error ?? "AGV 요청 실패";
            throw new HttpRequestException(message, null, response.StatusCode);
        }
        if (action == "heartbeat") return new();
        var state = await response.Content.ReadFromJsonAsync<AgvState>(Options, cancellation);
        if (state is null || state.Mode != "DEMO" || state.Device is null || state.Eligible is null || state.Eligible.Any(item => item is null))
            throw new JsonException("AGV 상태 응답 형식이 올바르지 않습니다.");
        return state;
    }
}
