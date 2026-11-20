using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace RobotFactory.Admin.Services;

public sealed record FactoryOrder
{
    public string Id { get; init; } = "";
    public string Company { get; init; } = "";
    public string Phone { get; init; } = "";
    public string Address { get; init; } = "";
    public string ModelName { get; init; } = "";
    public string TargetHeadId { get; init; } = "";
    public string TargetArmLId { get; init; } = "";
    public string TargetArmRId { get; init; } = "";
    public int? TargetStation { get; init; }
    public string FactoryStatus { get; init; } = "";
    public DateTimeOffset? CreatedAt { get; init; }
    public string Parts => $"머리 {TargetHeadId} · 왼팔 {TargetArmLId} · 오른팔 {TargetArmRId}";
    public string Destination => TargetStation switch { 3 => "3 · 서울 HUB", 4 => "4 · 평택 HUB", 5 => "5 · 수원 HUB", 6 => "6 · 오류1", 7 => "7 · 오류2", _ => "목적지 미배정" };
    public string ReceivedTime => CreatedAt?.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") ?? "접수 시각 없음";
    public string State => FactoryStatus switch
    {
        "PENDING" => "접수 · 생산 대기",
        "IN_PROGRESS" => "생산 중",
        "COMPLETED" => "완료",
        "CANCELED" => "취소",
        _ => FactoryStatus
    };
}

public sealed record FactoryPallet
{
    public string PalletId { get; init; } = "";
    public string CurrentStatus { get; init; } = "";
    public int CycleCount { get; init; }
    public DateTimeOffset? UpdatedAt { get; init; }
    public string UpdatedTime => UpdatedAt?.ToLocalTime().ToString("HH:mm:ss") ?? "—";
    public string UpdatedDateTime => UpdatedAt?.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss") ?? "갱신 시각 없음";
}

public sealed class OrderApiClient
{
    private static readonly HttpClient Client = ApiConnection.CreateClient(TimeSpan.FromSeconds(5));
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
    };

    private sealed record OrderList([property: JsonPropertyName("orders")] List<FactoryOrder>? Orders);
    private sealed record PalletList([property: JsonPropertyName("pallets")] List<FactoryPallet>? Pallets);
    private sealed record OrderResult([property: JsonPropertyName("order")] FactoryOrder? Order);

    public async Task<FactoryOrder> CancelOrderAsync(Uri server, string token, string orderId, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post,
            new Uri(server, $"/api/orders/{Uri.EscapeDataString(orderId)}/cancel"));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await Client.SendAsync(request, cancellationToken);
        if (!response.IsSuccessStatusCode)
            throw await ApiConnection.CreateErrorAsync(response,
                $"주문 드롭 실패 ({(int)response.StatusCode})", cancellationToken);
        var result = await response.Content.ReadFromJsonAsync<OrderResult>(JsonOptions, cancellationToken);
        if (result?.Order is not { } order || order.Id != orderId)
            throw new JsonException("주문 드롭 응답을 확인할 수 없습니다.");
        return order;
    }

    public async Task<IReadOnlyList<FactoryOrder>> GetOrdersAsync(Uri server, string token, CancellationToken cancellationToken)
    {
        var result = await GetAsync<OrderList>(server, "/api/orders", token, cancellationToken);
        if (result.Orders is null || result.Orders.Any(order => order is null || string.IsNullOrWhiteSpace(order.Id) || string.IsNullOrWhiteSpace(order.FactoryStatus)))
            throw new JsonException("서버의 주문 목록 형식이 올바르지 않습니다.");
        return result.Orders;
    }

    public async Task<IReadOnlyList<FactoryPallet>> GetPalletsAsync(Uri server, string token, CancellationToken cancellationToken)
    {
        var result = await GetAsync<PalletList>(server, "/api/pallets", token, cancellationToken);
        if (result.Pallets is null || result.Pallets.Any(pallet => pallet is null || string.IsNullOrWhiteSpace(pallet.PalletId) || string.IsNullOrWhiteSpace(pallet.CurrentStatus) || pallet.CycleCount < 0))
            throw new JsonException("서버의 팔레트 목록 형식이 올바르지 않습니다.");
        return result.Pallets;
    }

    private async Task<T> GetAsync<T>(Uri server, string path, string token, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(server, path));
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await Client.SendAsync(request, cancellationToken);
        if (response.StatusCode == HttpStatusCode.Unauthorized)
            throw new HttpRequestException("로그인이 필요하거나 세션이 만료되었습니다.", null, response.StatusCode);
        if (response.StatusCode == HttpStatusCode.ServiceUnavailable)
            throw new HttpRequestException("주문 관제 API를 사용할 수 없습니다.", null, response.StatusCode);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<T>(JsonOptions, cancellationToken)
            ?? throw new JsonException("서버 응답을 확인할 수 없습니다.");
    }
}
