using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed class DashboardViewModel : INotifyPropertyChanged
{
    private readonly FactoryApiClient api=new();
    private readonly DispatcherTimer timer=new(){Interval=TimeSpan.FromSeconds(1)};
    private readonly DispatcherTimer frameTimer=new(){Interval=TimeSpan.FromMilliseconds(500)};
    private CancellationTokenSource? polling;
    private bool refreshing,framesRefreshing,busy,connected;
    private int pageCount=1,ticks;
    private string? nextCursor,ticketId;
    private readonly Dictionary<string,Guid> requestIds=[];
    private AdminOrderV1? selected;
    public string ServerAddress {get;set;}=Environment.GetEnvironmentVariable("MOBOT_API_URL") ?? "http://127.0.0.1:8000";
    public string AdminToken {get;set;}="";
    public bool IsSuperAdmin {get;set;}
    public event Action? SessionExpired;
    public ObservableCollection<AdminOrderV1> Orders {get;}=[];
    public ObservableCollection<PalletV1> Pallets {get;}=[];
    public ObservableCollection<StockV1> Stock {get;}=[];
    public ObservableCollection<HistoryV1> History {get;}=[];
    public ObservableCollection<AdminAccount> Accounts {get;}=[];
    public PlantV1? Plant {get;private set;}
    public InspectionV1? Inspection {get;private set;}
    public ImageSource? Cam1 {get;private set;}
    public ImageSource? Cam2 {get;private set;}
    public ImageSource? Cam3 {get;private set;}
    public ImageSource? Cam4 {get;private set;}
    public string ConnectionStatus {get;private set;}="로그인 후 연결합니다.";
    public string ActionStatus {get;private set;}="제작 수락 후 전체 준비 상태를 확인하세요.";
    public string EnvironmentLabel => api.Environment=="SIMULATION" ? "모의 검증 · 실물 장비 미사용" : "실물 모드 · 준비·완료 증거 확인";
    public string Summary => $"{Plant?.State ?? "연결 대기"} · {Plant?.Stage ?? "READY"}";
    public string CycleInfo => $"회차 {Plant?.CycleId ?? "대기"}";
    public string OrderCount => $"주문 {Orders.Count}건";
    public string PalletCount => $"팔레트 {Pallets.Count}개";
    public string Alert => !connected ? "서버 연결 끊김 · 마지막 수신 상태 표시 · 조작 차단" :
        Plant?.State=="RECOVERY_REQUIRED" ? "결과 미확인 · 상태와 실물을 대조한 후 현장 확인이 필요합니다." :
        Plant?.FeedQr?.State=="UNREADABLE" ? "투입 QR 3회 실패 · 벨트 정차 · 재판독이 필요합니다." :
        Plant?.FeedQr?.State=="SAVE_FAILED" ? "QR 판독 성공 · DB 저장 실패 · 저장만 재시도하세요." :
        Plant?.State=="NOT_READY" ? "세 장비와 카메라 4대·승인 프로필의 준비 상태를 확인하세요." :
        Inspection?.Verdict=="INDETERMINATE" ? "판정 불가 · 검사 사유 확인 · 오류2 구역 이송" : "현재 공정 경고 없음";
    public string InspectionSummary => Inspection is null ? "검사 결과 대기" : $"{Inspection.Verdict} · 실제 목적지 {Inspection.DispatchStation}\n{string.Join(", ",Inspection.Reasons)}";
    public string QrSummary => $"QR {SelectedOrder?.PalletId ?? "미배정"} · {Plant?.FeedQr?.State ?? "대기"} · 시도 {Plant?.FeedQr?.Attempt ?? 0}/3";
    public bool CanAccept => connected && !busy && selected?.FactoryStatus=="PENDING";
    public bool CanCancel => connected && !busy && selected is { CycleId:null } && selected.FactoryStatus is "PENDING" or "QUEUED";
    public bool CanStart => connected && !busy && Plant?.State=="READY";
    public bool CanPause => connected && !busy && Plant?.State is "READY" or "RUNNING" or "NOT_READY";
    public bool CanResume => connected && !busy && Plant?.State=="PAUSED";
    public bool CanReadQr => connected && !busy && Plant?.FeedQr?.State=="UNREADABLE";
    public bool CanSaveQr => connected && !busy && Plant?.FeedQr?.State=="SAVE_FAILED";
    public bool CanLoadMore => connected && !busy && nextCursor is not null && pageCount<20;
    public bool CanAdminister => connected && !busy && IsSuperAdmin;
    public string StockKey {get;set;}="ARM_1";
    public string AddedUnits {get;set;}="1";
    public string NewLoginId {get;set;}="";
    public string NewDisplayName {get;set;}="";
    public string NewRole {get;set;}="ADMIN";
    public string RoleToSet {get;set;}="ADMIN";
    public AdminAccount? SelectedAccount {get;set;}
    private HistoryV1? selectedHistory;
    public HistoryV1? SelectedHistory
    {
        get=>selectedHistory;
        set {selectedHistory=value;if(value?.CommandId is {} command){ReconcileCommandId=command;Notify(nameof(ReconcileCommandId));}Notify();}
    }
    public string ReconcileCommandId {get;set;}="";
    public string ReconcileObservation {get;set;}="UNRESOLVED";
    public string ReconcileReason {get;set;}="";
    public IReadOnlyList<string> Observations {get;}=["UNRESOLVED","COMPLETED","NOT_EXECUTED"];
    public bool CanReconcile=>connected && !busy && Plant?.State=="RECOVERY_REQUIRED";
    public bool CanViewImage=>connected && Inspection?.ImageRef is not null;
    public bool CanDeleteImage=>connected && !busy && IsSuperAdmin && Inspection?.ArchiveId is not null && selected?.FactoryStatus is "COMPLETED" or "ERROR_DELIVERED";
    public IReadOnlyList<string> Roles {get;}=["ADMIN","SUPER_ADMIN"];
    public AdminOrderV1? SelectedOrder
    {
        get=>selected;
        set {if(selected?.OrderId!=value?.OrderId){History.Clear();Inspection=null;}selected=value;Notify();NotifyAll();}
    }
    public DashboardViewModel()
    {
        timer.Tick+=async(_,_)=>await RefreshAsync();
        frameTimer.Tick+=async(_,_)=>await FramesAsync();
    }
    public async Task StartAsync()
    {
        Stop();
        if(!Uri.TryCreate(ServerAddress,UriKind.Absolute,out var address) || address.Scheme is not ("http" or "https") || string.IsNullOrWhiteSpace(AdminToken))return;
        polling=new();timer.Start();frameTimer.Start();await RefreshAsync();
    }
    public void Stop()
    {
        timer.Stop();frameTimer.Stop();polling?.Cancel();polling?.Dispose();polling=null;connected=false;NotifyAll();
    }
    public void ClearSession()
    {
        Stop();AdminToken="";Orders.Clear();Pallets.Clear();Stock.Clear();History.Clear();Accounts.Clear();SelectedOrder=null;
        Cam1=Cam2=Cam3=Cam4=null;Plant=null;requestIds.Clear();NotifyAll();
    }
    private static void Replace<T>(ObservableCollection<T> target,IEnumerable<T> values)
    {
        var array=values.ToArray();if(target.SequenceEqual(array))return;
        target.Clear();foreach(var item in array)target.Add(item);
    }
    public async Task RefreshAsync()
    {
        if(refreshing || polling is null)return;
        var cancellation=polling.Token;refreshing=true;
        try
        {
            var server=new Uri(ServerAddress);
            var plants=api.RequestAsync<PlantV1>(server,AdminToken,"plant",null,cancellation);
            var pallets=api.RequestAsync<PalletPageV1>(server,AdminToken,"pallets?limit=100",null,cancellation);
            var stocks=api.RequestAsync<StockPageV1>(server,AdminToken,"stock?limit=100",null,cancellation);
            var orders=new List<AdminOrderV1>();string? cursor=null;
            for(int i=0;i<pageCount;i++)
            {
                var page=await api.RequestAsync<OrderPageV1>(server,AdminToken,"orders?limit=50"+(cursor is null ? "":"&cursor="+Uri.EscapeDataString(cursor)),null,cancellation);
                orders.AddRange(page.Orders);cursor=page.NextCursor;if(cursor is null)break;
            }
            await Task.WhenAll(plants,pallets,stocks);cancellation.ThrowIfCancellationRequested();
            var plant=await plants;
            if(plant.Devices is null || plant.CameraReady is null)throw new JsonException("공정 응답을 확인하세요.");
            Plant=plant;nextCursor=cursor;
            var selectedId=selected?.OrderId;
            Replace(Orders,orders);Replace(Pallets,(await pallets).Pallets);Replace(Stock,(await stocks).Items);
            selected=Orders.FirstOrDefault(item=>item.OrderId==selectedId) ?? Orders.FirstOrDefault();Notify(nameof(SelectedOrder));
            connected=true;
            ConnectionStatus=$"서버·DB 연결 · {DateTime.Now:HH:mm:ss} 갱신 · 규약 0.1.0";
            if(ticketId is not null)
            {
                var ticket=await api.RequestAsync<TicketV1>(server,AdminToken,"plant/actions/"+ticketId,null,cancellation);
                ActionStatus=$"공정 요청 {ticket.State} · {ticket.PlantState}";
                if(ticket.State is "COMPLETED" or "FAILED" or "STOPPED")ticketId=null;
            }
            if(selected is not null && ++ticks%2==0)await LoadHistoryAsync(server,cancellation);
            if(IsSuperAdmin && ticks%5==0)
            {
                var page=await api.RequestAsync<AccountPageV1>(server,AdminToken,"accounts?limit=100",null,cancellation);
                cancellation.ThrowIfCancellationRequested();Replace(Accounts,page.Accounts);
            }
        }
        catch(OperationCanceledException) when(cancellation.IsCancellationRequested){}
        catch(Exception error) when(error is HttpRequestException or JsonException or IOException or OperationCanceledException)
        {
            connected=false;ConnectionStatus="연결 끊김 · "+error.Message;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();
        }
        finally {refreshing=false;NotifyAll();}
    }
    private async Task LoadHistoryAsync(Uri server,CancellationToken cancellation)
    {
        var id=selected!.OrderId;
        var page=await api.RequestAsync<HistoryPageV1>(server,AdminToken,$"orders/{id}/history?limit=50",null,cancellation);
        cancellation.ThrowIfCancellationRequested();if(selected?.OrderId!=id)return;
        Replace(History,page.Events);
        var inspection=page.Events.FirstOrDefault(item=>item.InspectionId is not null);
        if(inspection?.InspectionId is string inspectionId)
        {
            var data=await api.RequestAsync<InspectionV1>(server,AdminToken,"inspections/"+inspectionId,null,cancellation);
            cancellation.ThrowIfCancellationRequested();if(selected?.OrderId==id)Inspection=data;
        }
    }
    private async Task FramesAsync()
    {
        if(framesRefreshing || !connected || polling is null)return;
        var cancellation=polling.Token;framesRefreshing=true;
        try
        {
            var server=new Uri(ServerAddress);
            var frames=await Task.WhenAll(new[]{"cam1","cam2","cam3","cam4"}.Select(camera=>api.FrameAsync(server,AdminToken,camera,cancellation)));
            cancellation.ThrowIfCancellationRequested();
            ImageSource? Decode(byte[]? bytes)
            {
                if(bytes is null)return null;using var stream=new MemoryStream(bytes);var bitmap=new BitmapImage();
                bitmap.BeginInit();bitmap.CacheOption=BitmapCacheOption.OnLoad;bitmap.StreamSource=stream;bitmap.EndInit();bitmap.Freeze();return bitmap;
            }
            Cam1=Decode(frames[0]);Cam2=Decode(frames[1]);Cam3=Decode(frames[2]);Cam4=Decode(frames[3]);
        }
        catch(OperationCanceledException)when(cancellation.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException or NotSupportedException or FormatException or COMException)
        {
            Cam1=Cam2=Cam3=Cam4=null;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();
        }
        finally{framesRefreshing=false;foreach(var name in new[]{nameof(Cam1),nameof(Cam2),nameof(Cam3),nameof(Cam4)})Notify(name);}
    }
    private async Task ActAsync(string key,Func<Guid,CancellationToken,Task> action)
    {
        if(busy || polling is null || !connected)return;
        var cancellation=polling.Token;busy=true;NotifyAll();
        if(!requestIds.TryGetValue(key,out var id)){id=Guid.NewGuid();requestIds[key]=id;}
        try{await action(id,cancellation);cancellation.ThrowIfCancellationRequested();requestIds.Remove(key);await RefreshAsync();}
        catch(OperationCanceledException)when(cancellation.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {
            ActionStatus=error.Message;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();
        }
        finally{busy=false;NotifyAll();}
    }
    public Task AcceptAsync()=>selected is { } order ? ActAsync("accept:"+order.OrderId,async(id,ct)=>{await api.RequestAsync<AdminOrderV1>(new Uri(ServerAddress),AdminToken,$"orders/{order.OrderId}/accept",new{request_id=id},ct);ActionStatus="제작 수락을 저장했습니다.";}):Task.CompletedTask;
    public Task CancelAsync()=>selected is { } order ? ActAsync("cancel:"+order.OrderId,async(id,ct)=>{await api.RequestAsync<AdminOrderV1>(new Uri(ServerAddress),AdminToken,$"orders/{order.OrderId}/cancel",new{request_id=id},ct);ActionStatus="주문 취소·재고 복구를 저장했습니다.";}):Task.CompletedTask;
    public Task PlantActionAsync(string action)=>ActAsync("plant:"+action,async(id,ct)=>{var ticket=await api.RequestAsync<TicketV1>(new Uri(ServerAddress),AdminToken,"plant/actions",new{request_id=id,action},ct);ticketId=ticket.RequestId;ActionStatus=$"요청 접수 {ticket.State} · 완료 대기";});
    public Task RetryQrAsync(string action)
    {
        if(Plant is not {CycleId: string cycle,FeedQr: { } qr})return Task.CompletedTask;
        return ActAsync("qr:"+cycle+qr.ReadId+action,async(id,ct)=>{await api.RequestAsync<JsonElement>(new Uri(ServerAddress),AdminToken,$"cycles/{cycle}/qr-retry",new{request_id=id,action,previous_read_id=qr.ReadId},ct);ActionStatus="QR 재시도를 접수했습니다.";});
    }
    public async Task LoadMoreAsync(){if(!CanLoadMore)return;pageCount++;await RefreshAsync();}
    public Task RestockAsync()
    {
        if(!int.TryParse(AddedUnits,out var count) || count<1){ActionStatus="입고 수량은 양의 정수입니다.";NotifyAll();return Task.CompletedTask;}
        return ActAsync("restock:"+StockKey+":"+count,async(id,ct)=>{await api.RequestAsync<StockV1>(new Uri(ServerAddress),AdminToken,"stock/restocks",new{request_id=id,stock_key=StockKey,added_units=count},ct);ActionStatus="재입고를 저장했습니다.";});
    }
    public Task CreateAccountAsync(string password)=>ActAsync("account:"+NewLoginId+NewDisplayName+NewRole+Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(password))),async(id,ct)=>
    {
        await api.RequestAsync<AdminAccount>(new Uri(ServerAddress),AdminToken,"accounts",new{request_id=id,login_id=NewLoginId,display_name=NewDisplayName,role=NewRole,initial_password=password},ct);ActionStatus="계정을 생성했습니다.";
    });
    public Task ChangeRoleAsync()=>SelectedAccount is { } account ? ActAsync("role:"+account.AccountId+RoleToSet,async(id,ct)=>{await api.RequestAsync<AdminAccount>(new Uri(ServerAddress),AdminToken,$"accounts/{account.AccountId}/role",new{request_id=id,role=RoleToSet},ct,HttpMethod.Patch);ActionStatus="권한 변경·기존 세션 철회 완료";}):Task.CompletedTask;
    public Task ReconcileAsync()=>ActAsync("reconcile:"+ReconcileCommandId+ReconcileObservation+ReconcileReason,async(id,ct)=>
    {
        var result=await api.RequestAsync<JsonElement>(new Uri(ServerAddress),AdminToken,$"commands/{ReconcileCommandId}/reconcile",new{request_id=id,observation=ReconcileObservation,reason=ReconcileReason},ct);
        ActionStatus=result.GetProperty("requires_review").GetBoolean()?"현장 확인 기록 완료 · 장비 증거 대조가 더 필요합니다.":"현장 대조 완료 · 전체 장비 정지 확인 후 해제할 수 있습니다.";
    });
    public async Task<ImageSource?> InspectionImageAsync()
    {
        if(!CanViewImage || polling is null || Inspection is null)return null;
        try
        {
            var bytes=await api.ImageAsync(new Uri(ServerAddress),AdminToken,$"inspections/{Inspection.InspectionId}/image",polling.Token);
            if(bytes is null){ActionStatus="보관된 사진이 없습니다.";NotifyAll();return null;}
            using var stream=new MemoryStream(bytes);var image=new BitmapImage();image.BeginInit();image.CacheOption=BitmapCacheOption.OnLoad;image.StreamSource=stream;image.EndInit();image.Freeze();return image;
        }
        catch(Exception error)when(error is HttpRequestException or IOException or OperationCanceledException or NotSupportedException or FormatException or COMException){ActionStatus=error.Message;NotifyAll();return null;}
    }
    public Task DeleteImageAsync()=>Inspection?.ArchiveId is string record ? ActAsync("delete:"+record,async(id,ct)=>{await api.RequestAsync<JsonElement>(new Uri(ServerAddress),AdminToken,$"archive/{record}/delete",new{request_id=id,reason="관리자 보관 검사 사진 삭제"},ct);ActionStatus="보관 사진 삭제·감사 기록 완료";}):Task.CompletedTask;
    private void NotifyAll()
    {
        foreach(var name in new[]{nameof(Plant),nameof(Summary),nameof(CycleInfo),nameof(EnvironmentLabel),nameof(ConnectionStatus),nameof(ActionStatus),nameof(Alert),nameof(Inspection),nameof(InspectionSummary),nameof(QrSummary),nameof(CanAccept),nameof(CanCancel),nameof(CanStart),nameof(CanPause),nameof(CanResume),nameof(CanReadQr),nameof(CanSaveQr),nameof(CanLoadMore),nameof(CanAdminister),nameof(OrderCount),nameof(PalletCount)})Notify(name);
        Notify(nameof(CanReconcile));Notify(nameof(CanViewImage));Notify(nameof(CanDeleteImage));
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName]string? name=null)=>PropertyChanged?.Invoke(this,new(name));
}
