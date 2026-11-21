using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace RobotFactory.Admin.Services;

public sealed record BuildSpec(string TargetHeadId,string TargetArmLId,string TargetArmRId);
public sealed record AdminOrderV1(string OrderId,BuildSpec Spec,string Company,string Phone,string Address,int TargetStation,int? DispatchStation,string FactoryStatus,long? ApprovalSeq,string? CycleId,string? PalletId,DateTimeOffset CreatedAt,DateTimeOffset UpdatedAt)
{
    public string ModelName => $"{Spec.TargetHeadId}-{Spec.TargetArmLId}-{Spec.TargetArmRId}";
    public string State => FactoryStatus switch { "PENDING"=>"제작 수락 대기","QUEUED"=>$"생산 대기 · 순번 {ApprovalSeq}","IN_PROGRESS"=>"제작 중","COMPLETED"=>"정상 완료","ERROR_DELIVERED"=>"오류품 하역 완료","CANCELLED"=>"취소",_=>FactoryStatus };
    public string Destination => $"정상 HUB {TargetStation} · 실제 {DispatchStation?.ToString() ?? "검사 대기"}";
    public string ReceivedTime => CreatedAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss");
}
public sealed record PalletV1(string PalletId,string? CycleId,string Stage,DateTimeOffset UpdatedAt)
{
    public string UpdatedTime => UpdatedAt.ToLocalTime().ToString("HH:mm:ss");
}
public sealed record OrderPageV1(List<AdminOrderV1> Orders,string? NextCursor);
public sealed record PalletPageV1(List<PalletV1> Pallets,string? NextCursor);
public sealed record DeviceV1(string DeviceId,string BootId,string Mode,string State,string? CycleId,string? ActiveCommandId,string? LastCompletedCommandId,DateTimeOffset ObservedAt,bool Online);
public sealed record FeedQrV1(string ReadId,int Attempt,string State);
public sealed record PlantV1(string State,string Stage,string? CycleId,List<DeviceV1> Devices,Dictionary<string,bool> CameraReady,DateTimeOffset UpdatedAt,FeedQrV1? FeedQr);
public sealed record StockV1(string StockKey,int AvailableUnits);
public sealed record StockPageV1(List<StockV1> Items,string? NextCursor);
public sealed record AccountPageV1(List<AdminAccount> Accounts,string? NextCursor);
public sealed record TicketV1(string RequestId,string State,string PlantState);
public sealed record HistoryV1(string RecordId,string OrderId,string? CycleId,string? CommandId,string? InspectionId,DateTimeOffset RecordedAt,string Stage,string Kind,string Message)
{
    public string Time=>RecordedAt.ToLocalTime().ToString("HH:mm:ss");
}
public sealed record HistoryPageV1(List<HistoryV1> Events,string? NextCursor);
public sealed record InspectionV1(string OrderId,string InspectionId,string CycleId,string ExpectedPalletId,string Verdict,List<string> Reasons,int DispatchStation,DateTimeOffset? CapturedAt,string? FrameId,string? ImageRef,string? ArchiveId=null);
public sealed record ApiFaultV1(string Code,string Message,string Retry,string TraceId);

public sealed class FactoryApiClient
{
    private static readonly HttpClient Client=ApiConnection.CreateClient(TimeSpan.FromSeconds(5));
    public static readonly JsonSerializerOptions Json=new(){PropertyNamingPolicy=JsonNamingPolicy.SnakeCaseLower};
    public string Environment {get;private set;}="HARDWARE";
    public async Task<T> RequestAsync<T>(Uri server,string token,string path,object? body,CancellationToken cancellation,HttpMethod? method=null)
    {
        using var request=new HttpRequestMessage(method ?? (body is null ? HttpMethod.Get:HttpMethod.Post),new Uri(server,"/api/v1/"+path));
        if(!string.IsNullOrEmpty(token))request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",token);
        if(body is not null)request.Content=JsonContent.Create(body);
        using var response=await Client.SendAsync(request,cancellation);
        if(response.Headers.TryGetValues("X-Mobot-Environment",out var values))Environment=values.First();
        if(!response.IsSuccessStatusCode)
        {
            var fault=await response.Content.ReadFromJsonAsync<ApiFaultV1>(Json,cancellation);
            throw new HttpRequestException($"{fault?.Message ?? "서버 요청 실패"} [{fault?.Code}] · {fault?.Retry}",null,response.StatusCode);
        }
        var result=await response.Content.ReadFromJsonAsync<T>(Json,cancellation) ?? throw new JsonException("서버 응답이 비어 있습니다.");
        bool OrderValid(AdminOrderV1? order)=>order is not null && Guid.TryParse(order.OrderId,out _) && order.Spec is not null &&
            order.Spec.TargetHeadId is "H1" or "H2" && order.Spec.TargetArmLId is "L1" or "L2" or "L3" && order.Spec.TargetArmRId is "R1" or "R2" or "R3";
        bool AccountValid(AdminAccount? account)=>account is not null && !string.IsNullOrWhiteSpace(account.AccountId) && !string.IsNullOrWhiteSpace(account.DisplayName) && account.Role is "ADMIN" or "SUPER_ADMIN";
        bool valid=result switch
        {
            AdminOrderV1 order=>OrderValid(order),
            OrderPageV1 page=>page.Orders is not null && page.Orders.All(OrderValid),
            PalletPageV1 page=>page.Pallets is not null && page.Pallets.All(p=>p is not null && !string.IsNullOrEmpty(p.PalletId)),
            PlantV1 plant=>plant.Devices is not null && plant.Devices.All(d=>d is not null && d.DeviceId is "LINE" or "ARM" or "AGV") && plant.CameraReady is not null && new[]{"cam1","cam2","cam3","cam4"}.All(plant.CameraReady.ContainsKey),
            StockPageV1 page=>page.Items is not null && page.Items.All(s=>s is not null && s.AvailableUnits>=0),
            AccountPageV1 page=>page.Accounts is not null && page.Accounts.All(AccountValid),
            AdminAccount account=>AccountValid(account),
            HistoryPageV1 page=>page.Events is not null && page.Events.All(h=>h is not null && Guid.TryParse(h.RecordId,out _)),
            InspectionV1 inspection=>inspection.Reasons is not null && Guid.TryParse(inspection.InspectionId,out _),
            JsonElement element=>element.ValueKind==JsonValueKind.Object,
            _=>true
        };
        if(!valid)throw new JsonException("서버 응답 필수 필드와 규약을 확인하세요.");
        return result;
    }
    public async Task<byte[]?> FrameAsync(Uri server,string token,string camera,CancellationToken cancellation)
        =>await ImageAsync(server,token,"cameras/"+camera.ToUpperInvariant()+"/frame",cancellation);
    public async Task<byte[]?> ImageAsync(Uri server,string token,string path,CancellationToken cancellation)
    {
        using var request=new HttpRequestMessage(HttpMethod.Get,new Uri(server,"/api/v1/"+path));
        request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",token);
        using var response=await Client.SendAsync(request,cancellation);
        if(response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.ServiceUnavailable)return null;
        response.EnsureSuccessStatusCode();
        if(response.Content.Headers.ContentType?.MediaType!="image/jpeg")throw new IOException("카메라 응답이 JPEG가 아닙니다.");
        return await response.Content.ReadAsByteArrayAsync(cancellation);
    }
}
