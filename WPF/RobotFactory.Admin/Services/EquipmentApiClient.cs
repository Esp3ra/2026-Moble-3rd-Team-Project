using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace RobotFactory.Admin.Services;

public sealed record EquipmentTrial
{
    public string? Id { get; init; }
    public string? PalletId { get; init; }
    public bool? QrOk { get; init; }
    public bool? QrAck { get; init; }
    public bool? MachineDone { get; init; }
    public bool? EjectionConfirmed { get; init; }
}

public sealed record EquipmentRun
{
    public string RunId { get; init; } = "";
    public string JobId { get; init; } = "";
    public string ExpectedPalletId { get; init; } = "";
    public string? PalletId { get; init; }
    public int? MappingId { get; init; }
    public string State { get; init; } = "";
    public string DatabaseStatus { get; init; } = "waiting";
    public string DatabaseError { get; init; } = "";
    public DateTimeOffset? FinishedAt { get; init; }
    public EquipmentTrial Trial { get; init; } = new();
}

public sealed record EquipmentEvent
{
    public string At { get; init; } = "";
    [JsonPropertyName("event")]
    public string Name { get; init; } = "";
    public string? Text { get; init; }
}

public sealed record EquipmentTimings
{
    public Dictionary<string, int> Values { get; init; } = [];
    public string Status { get; init; } = "waiting";
}

public sealed record EquipmentState
{
    public string HardwareMode { get; init; } = "DEMO";
    public string Mode { get; init; } = "DEMO";
    public string State { get; init; } = "IDLE";
    public string Error { get; init; } = "";
    public bool CanStart { get; init; }
    public bool CanConfirm { get; init; }
    public bool CanRetry { get; init; }
    public bool IsOwner { get; init; }
    public bool IrClear { get; init; }
    public bool Ir2Clear { get; init; }
    public EquipmentRun? Run { get; init; }
    public EquipmentTrial Trial { get; init; } = new();
    public EquipmentTimings Timings { get; init; } = new();
    public List<EquipmentEvent> Events { get; init; } = [];
}

public sealed class EquipmentApiClient
{
    private static readonly HttpClient Client = new() { Timeout = TimeSpan.FromSeconds(5) };
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private sealed record ApiError([property: JsonPropertyName("error")] string? Error);

    private static async Task<HttpResponseMessage> SendAsync(Uri server, string token, string path,
        HttpMethod method, object? payload, CancellationToken cancellation)
    {
        using var request = new HttpRequestMessage(method, new Uri(server, "/api/equipment/" + path));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (payload is not null) request.Content = JsonContent.Create(payload);
        var response = await Client.SendAsync(request, cancellation);
        if (response.IsSuccessStatusCode) return response;
        try
        {
            string message = $"장비 요청 실패 ({(int)response.StatusCode})";
            if (response.Content.Headers.ContentType?.MediaType == "application/json")
                message = (await response.Content.ReadFromJsonAsync<ApiError>(Options, cancellation))?.Error ?? message;
            throw new HttpRequestException(message, null, response.StatusCode);
        }
        finally { response.Dispose(); }
    }

    public async Task<EquipmentState> GetStateAsync(Uri server, string token, CancellationToken cancellation)
    {
        using var response = await SendAsync(server, token, "state", HttpMethod.Get, null, cancellation);
        return await ReadStateAsync(response, cancellation);
    }

    public async Task<EquipmentState> CommandAsync(Uri server, string token, string action, object payload, CancellationToken cancellation)
    {
        using var response = await SendAsync(server, token, action, HttpMethod.Post, payload, cancellation);
        return await ReadStateAsync(response, cancellation);
    }

    public async Task HeartbeatAsync(Uri server, string token, CancellationToken cancellation)
    {
        using var response = await SendAsync(server, token, "heartbeat", HttpMethod.Post, new { }, cancellation);
    }

    public async Task<byte[]?> GetFrameAsync(Uri server, string token, CancellationToken cancellation)
    {
        using var response = await SendAsync(server, token, "frame.jpg", HttpMethod.Get, null, cancellation);
        if (response.Content.Headers.ContentType?.MediaType != "image/jpeg")
            throw new JsonException("장비 영상 응답이 JPEG 형식이 아닙니다.");
        return await response.Content.ReadAsByteArrayAsync(cancellation);
    }

    private static async Task<EquipmentState> ReadStateAsync(HttpResponseMessage response, CancellationToken cancellation)
    {
        var state = await response.Content.ReadFromJsonAsync<EquipmentState>(Options, cancellation);
        if (state is null || state.Mode is not ("DEMO" or "REMOTE") || state.Trial is null || state.Timings is null ||
            state.Timings.Values is null || state.Events is null || state.Events.Any(item => item is null) || string.IsNullOrWhiteSpace(state.State))
            throw new JsonException("장비 상태 응답 형식이 올바르지 않습니다.");
        return state;
    }
}
