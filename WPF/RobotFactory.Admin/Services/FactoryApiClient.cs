using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace RobotFactory.Admin.Services;

public sealed record BuildSpec(string TargetHeadId,string TargetArmLId,string TargetArmRId);
public static class HubNames
{
    public static string Label(int station)=>station switch{3=>"3번 서울 HUB",4=>"4번 대전 HUB",5=>"5번 천안 HUB",6=>"6번 오류1",7=>"7번 오류2",_=>"목적지 미배정"};
}
public sealed record AdminOrderV1(string OrderId,BuildSpec Spec,string Company,string Phone,string Address,int TargetStation,int? DispatchStation,string FactoryStatus,long? ApprovalSeq,string? CycleId,string? PalletId,DateTimeOffset CreatedAt,DateTimeOffset UpdatedAt)
{
    public string ModelName => $"{Spec.TargetHeadId}-{Spec.TargetArmLId}-{Spec.TargetArmRId}";
    public string State => FactoryStatus switch { "PENDING"=>"제작 수락 대기","QUEUED"=>$"생산 대기 · 순번 {ApprovalSeq}","IN_PROGRESS"=>"제작 중","COMPLETED"=>"정상 완료","ERROR_DELIVERED"=>"오류품 하역 완료","CANCELLED"=>"취소",_=>FactoryStatus };
    public string Destination => $"정상 {HubNames.Label(TargetStation)} · 실제 {(DispatchStation is int station ? HubNames.Label(station) : "검사 대기")}";
    public string ReceivedTime => CreatedAt.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss");
}
public sealed record PalletV1(string PalletId,string? CycleId,string Stage,DateTimeOffset UpdatedAt)
{
    public string UpdatedTime => UpdatedAt.ToLocalTime().ToString("HH:mm:ss");
}
public sealed record OrderPageV1(List<AdminOrderV1> Orders,string? NextCursor);
public sealed record PalletPageV1(List<PalletV1> Pallets,string? NextCursor);
public sealed record DeviceV1(string DeviceId,string BootId,string Mode,string State,string? CycleId,string? ActiveCommandId,string? LastCompletedCommandId,DateTimeOffset ObservedAt,bool Online)
{
    public string DisplayState=>State;
    public string DisplayOnline=>Online ? " · 온라인" : " · 오프라인";
}
public sealed record FeedQrV1(string ReadId,int Attempt,string State);
public sealed record PlantV1(string State,string Stage,string? CycleId,List<DeviceV1> Devices,Dictionary<string,bool> CameraReady,DateTimeOffset UpdatedAt,FeedQrV1? FeedQr);
public sealed record StockV1(string StockKey,int AvailableUnits);
public sealed record StockPageV1(List<StockV1> Items,string? NextCursor);
public sealed record AccountPageV1(List<AdminAccount> Accounts,string? NextCursor);
public sealed record TicketV1(string RequestId,string State,string PlantState);
public sealed record QrTicketV1(Guid? RequestId,Guid? CycleId,string State,Guid? ReadId);
public sealed record HistoryV1(string RecordId,string OrderId,string? CycleId,string? CommandId,string? InspectionId,DateTimeOffset RecordedAt,string Stage,string Kind,string Message)
{
    public string Time=>RecordedAt.ToLocalTime().ToString("HH:mm:ss");
}
public sealed record HistoryPageV1(List<HistoryV1> Events,string? NextCursor);
public sealed record InspectionV1(string OrderId,string InspectionId,string CycleId,string ExpectedPalletId,string Verdict,List<string> Reasons,int? DispatchStation,DateTimeOffset? CapturedAt,string? FrameId,string? ImageRef,string? ArchiveId=null,int? AttemptNumber=null,string? PreviousInspectionId=null,string? ReworkCommandId=null);
public sealed record ApiFaultV1(string Code,string Message,string Retry,string TraceId);
public sealed record CameraFrameV1(byte[] Bytes,string? CapturedAt);
public sealed record QrRoiV1(string State,string? PalletId,string Text,List<string> DecodedValues,string? Method,string? FrameId,DateTimeOffset? CapturedAt,int Revision,List<double> Roi,List<int> Resolution,List<int> RequestedResolution,double RequestedFps,double CaptureFps,double DecodeFps);
public sealed class FactoryApiClient
{
    private static readonly HttpClient SharedClient=ApiConnection.CreateClient(TimeSpan.FromSeconds(5));
    private readonly HttpClient Client;
    public FactoryApiClient(HttpClient? client=null){Client=client ?? SharedClient;}
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
            throw await ApiConnection.CreateErrorAsync(response,$"서버 요청 실패 (HTTP {(int)response.StatusCode})",cancellation);
        }
        var result=await response.Content.ReadFromJsonAsync<T>(Json,cancellation) ?? throw new JsonException("서버 응답이 비어 있습니다.");
        bool OrderValid(AdminOrderV1? order)=>order is not null && Guid.TryParse(order.OrderId,out _) && order.Spec is not null &&
            order.Spec.TargetHeadId is "H1" or "H2" && order.Spec.TargetArmLId is "L1" or "L2" or "L3" && order.Spec.TargetArmRId is "R1" or "R2" or "R3";
        bool AccountValid(AdminAccount? account)=>account is not null && !string.IsNullOrWhiteSpace(account.AccountId) && !string.IsNullOrWhiteSpace(account.DisplayName) && account.Role is "ADMIN" or "SUPER_ADMIN";
        bool valid=result switch
        {
            AdminOrderV1 order=>OrderValid(order),
            QrTicketV1 ticket=>ticket.RequestId.HasValue && ticket.CycleId.HasValue && ticket.ReadId.HasValue &&
                ticket.State is "ACCEPTED" or "RUNNING" or "COMPLETED" or "FAILED" or "STOPPED" or "UNKNOWN",
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
    public Task<CameraFrameV1?> LiveFrameAsync(Uri server,string token,string camera,CancellationToken cancellation)
        =>ImageDataAsync(server,token,"cameras/"+camera.ToUpperInvariant()+"/frame",cancellation);
    public async Task<byte[]?> ImageAsync(Uri server,string token,string path,CancellationToken cancellation)
        =>(await ImageDataAsync(server,token,path,cancellation))?.Bytes;
    private async Task<CameraFrameV1?> ImageDataAsync(Uri server,string token,string path,CancellationToken cancellation)
    {
        using var request=new HttpRequestMessage(HttpMethod.Get,new Uri(server,"/api/v1/"+path));
        request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",token);
        using var response=await Client.SendAsync(request,cancellation).ConfigureAwait(false);
        if(response.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.ServiceUnavailable)return null;
        response.EnsureSuccessStatusCode();
        if(response.Content.Headers.ContentType?.MediaType!="image/jpeg")throw new IOException("카메라 응답이 JPEG가 아닙니다.");
        return new(await response.Content.ReadAsByteArrayAsync(cancellation).ConfigureAwait(false),response.Headers.TryGetValues("X-Frame-Id",out var ids)?ids.First():response.Headers.TryGetValues("X-Captured-At",out var stamps)?stamps.First():null);
    }
}
