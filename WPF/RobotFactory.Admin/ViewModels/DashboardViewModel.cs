using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed record ConveyorStep(string Label,bool Active,bool Completed);

public sealed class DashboardViewModel : INotifyPropertyChanged
{
    private readonly FactoryApiClient api;
    private readonly DispatcherTimer timer=new(){Interval=TimeSpan.FromSeconds(1)};
    private readonly Dictionary<string,string?> frameStamps=[];
    private readonly Stopwatch frameClock=Stopwatch.StartNew();
    private int qrFrameCount;
    private double qrDisplayFps;
    private Task frameLoop=Task.CompletedTask;
    private bool frameAdvanced;
    private int pollingGeneration;
    private CancellationTokenSource? polling;
    private bool refreshing,framesRefreshing,busy,connected,wirelessConnected,plantFresh;
    private int pageCount=1,ticks;
    private string? nextCursor,ticketId;
    private readonly Dictionary<string,Guid> requestIds=[];
    private AdminOrderV1? selected;
    public string ServerAddress {get;set;}=Environment.GetEnvironmentVariable("MOBOT_API_URL") ?? "http://127.0.0.1:8000";
    public string AdminToken {get;set;}="";
    public bool IsSuperAdmin {get;set;}
    public event Action? SessionExpired;
    public void ReportSessionExpired()=>SessionExpired?.Invoke();
    public ObservableCollection<AdminOrderV1> Orders {get;}=[];
    public ObservableCollection<PalletV1> Pallets {get;}=[];
    public ObservableCollection<StockV1> Stock {get;}=[];
    public ObservableCollection<HistoryV1> History {get;}=[];
    public ObservableCollection<AdminAccount> Accounts {get;}=[];
    public PlantV1? Plant {get;private set;}
    public WirelessStatusV1? WirelessTest {get;private set;}
    public string WirelessTitle=>WirelessTest?.Transport=="LAN_HTTP_TEST"?"내부망 HTTP 통신·단독 제어 시험":"ngrok 무선 통신·단독 제어 시험";
    public string WirelessSummary=>WirelessTest is not {Enabled:true}?"무선 단독 시험 미활성":$"{WirelessTitle}\nPi {(WirelessTest.Online?"연결됨":"접속 대기")} · UNO {(WirelessTest.SerialReady?"식별 완료":WirelessTest.SerialConnected?"펌웨어 확인 필요":"미연결")} · {WirelessTest.Protocol}\n{WirelessTest.Message}";
    public bool CanWirelessPing=>wirelessConnected && !busy && WirelessTest is {Enabled:true,Online:true,Busy:false};
    public bool CanWirelessMotion=>CanWirelessPing && WirelessTest is {SerialReady:true} && WirelessTest.Protocol is "LEGACY_BENCH" or "WIRELESS_BENCH" or "WIRELESS_BENCH_10S" or "UNO_SIM_QR_V2";
    public bool CanWirelessBelt=>CanWirelessPing && WirelessTest is {SerialReady:true} && WirelessTest.Protocol is "WIRELESS_BENCH" or "WIRELESS_BENCH_10S";
    public bool CanWirelessStop=>wirelessConnected && WirelessTest is {Enabled:true};
    public InspectionV1? Inspection {get;private set;}
    public ImageSource? Cam1 {get;private set;}
    public ImageSource? Cam2 {get;private set;}
    public ImageSource? Cam3 {get;private set;}
    public ImageSource? Cam4 {get;private set;}
    public string ConnectionStatus {get;private set;}="로그인 후 연결합니다.";
    public string ActionStatus {get;private set;}="제작 수락 후 전체 준비 상태를 확인하세요.";
    public bool IsTransportTest=>api.Environment=="TRANSPORT_TEST";
    public string EnvironmentLabel => WirelessTest is {Enabled:true,Protocol:"UNO_SIM_QR_V2"}?"컨베이어 시험 · 실제 QR · 조립 3초 모의 · 주문 배정 없음":IsTransportTest ? "운송 시험 · 조립·YOLO 생략 · 시험 DB" : api.Environment=="SIMULATION" ? "모의 검증 · 실물 장비 미사용" : "실물 모드 · 준비·완료 증거 확인";
    public string AssemblyCameraTitle=>IsTransportTest?"CAM 02 · 위치 보정 생략":"CAM 02 · 조립 위치 보정";
    public string QrCameraTitle=>$"CAM 01 · 전체 영상 · {qrDisplayFps:0} fps · 90°";
    public string InspectionCameraTitle=>"CAM 03 · QR ROI 영상 · 90°";
    public string ProcessCaption=>WirelessTest is {Enabled:true,Protocol:"UNO_SIM_QR_V2"}?"컨베이어 시뮬레이션 · 배출 → IR1 정차 → QR 성공 대기 → 모의 조립 3초 → IR2 정차 · 실제 로봇팔 조립 아님":IsTransportTest?"운송 시험 · IR1 정차 후 조립 생략 · 암실 도착 후 검사 생략 · 실제 하역·2번·85° 복귀 확인":"규약 0.1.0 · 수락 순번 · QR·DB 저장 후 벨트 출발 · 조립 대기 자세 · 암실 검사 · 2번·85° 최종 복귀";
    public string StartLabel=>WirelessTest is {Enabled:true}?"컨베이어 작업 시작":"자동 운전 시작";
    public string ConveyorStatus=>!wirelessConnected || WirelessTest?.Online!=true?"Pi 접속 대기 / 연결 끊김":WirelessTest.Cycle?.Stage switch
    {
        "STARTING"=>"작업 시작 · UNO 회신 대기","DISPENSING"=>"액추에이터 전진 · 배출 중","REVERSAL"=>"액추에이터 방향 전환 대기",
        "TO_IR1"=>"액추에이터 복귀 + IR1까지 이송","IR1_DELAY"=>"IR1 감지 · 조립 위치까지 이송",
        "WAIT_QR"=>"벨트 정차 · QR 성공 대기","ROBOT_WAIT"=>"QR 확인 완료 · 로봇팔 모의 작업 3초",
        "TO_IR2"=>"모의 작업 완료 · IR2까지 이송","IR2_DELAY"=>"IR2 감지 · 끝 위치까지 이송",
        "WAIT_RETRACT"=>"벨트 정차 · 액추에이터 복귀 완료 대기","DONE"=>"컨베이어 시험 회차 완료",
        "STOPPED"=>"작업 중단 · 정지 요청","FAILED"=>"장비 오류 · 현장 확인 필요","UNKNOWN"=>"결과 미확인 · 현장 확인 필요",_=>"컨베이어 작업 대기"
    };
    public IReadOnlyList<DeviceV1> HeaderDevices=>WirelessTest is {Enabled:true}?[]:Plant?.Devices ?? [];
    public string ConveyorDetail=>WirelessTest is not {Enabled:true}?"":WirelessTest.Cycle is {} cycle?$"{cycle.ChangedAt.ToLocalTime():HH:mm:ss} · {cycle.Line} · QR {cycle.PalletId ?? "미인식"}{(cycle.QrConfirmed?" · UNO 확인":"")}":"UNO 시리얼 회신으로 단계 표시 · QR 성공 전 이송 차단";
    public IReadOnlyList<ConveyorStep> ConveyorSteps
    {
        get
        {
            if(WirelessTest is not {Enabled:true})return [];
            var current=WirelessTest.Online?WirelessTest.Cycle?.Stage:null;
            var step=current switch{"STARTING" or "DISPENSING" or "REVERSAL"=>0,"TO_IR1" or "IR1_DELAY"=>1,"WAIT_QR"=>2,"ROBOT_WAIT"=>3,"TO_IR2" or "IR2_DELAY"=>4,"WAIT_RETRACT"=>5,"DONE"=>6,_=>-1};
            var names=new[]{"1 · 배출","2 · IR1 정차","3 · QR 확인","4 · 조립 모의","5 · IR2 정차","6 · 완료"};
            return names.Select((name,index)=>new ConveyorStep(name,index==step,index<step)).ToArray();
        }
    }
    public string Summary => WirelessTest is {Enabled:true}?ConveyorStatus:$"{Plant?.State ?? "연결 대기"} · {Plant?.Stage ?? "READY"}";
    public string CycleInfo => WirelessTest is {Enabled:true}?$"시험 회차 {WirelessTest.Cycle?.CommandId ?? "대기"}":$"회차 {Plant?.CycleId ?? "대기"}";
    public string OrderCount => $"주문 {Orders.Count}건";
    public string PalletCount => $"팔레트 {Pallets.Count}개";
    public string Alert => !connected ? "서버 연결 끊김 · 마지막 수신 상태 표시 · 조작 차단" :
        WirelessTest is {Enabled:true}?WirelessTitle+" · 상단에서 컨베이어 작업 시작 · 주문 자동 배정 없음":
        Plant?.State=="RECOVERY_REQUIRED" ? "결과 미확인 · 상태와 실물을 대조한 후 현장 확인이 필요합니다." :
        Plant?.FeedQr?.State=="UNREADABLE" ? "투입 QR 3회 실패 · 벨트 정차 · 재판독이 필요합니다." :
        Plant?.FeedQr?.State=="SAVE_FAILED" ? "QR 판독 성공 · DB 저장 실패 · 저장만 재시도하세요." :
        Plant?.State=="NOT_READY" ? (IsTransportTest?"운송 시험 · LINE·AGV와 카메라 1·4의 실물 준비를 확인하세요.":"세 장비와 카메라 4대·승인 프로필의 준비 상태를 확인하세요.") :
        IsTransportTest ? "미조립·미검사 시험 운행 · 제품 합격 판정 없음" :
        Inspection?.Verdict=="INDETERMINATE" ? "판정 불가 · 검사 사유 확인 · 오류2 구역 이송" : "현재 공정 경고 없음";
    public string InspectionSummary => IsTransportTest ? "로봇팔 조립·암실 QR·YOLO 검사 생략 · 제품 합격 판정 없음" : Inspection is null ? "검사 결과 대기" : $"{Inspection.Verdict} · 실제 목적지 {Inspection.DispatchStation}\n{string.Join(", ",Inspection.Reasons)}";
    public string QrSummary => WirelessTest is {Enabled:true,Protocol:"UNO_SIM_QR_V2"}?$"QR {WirelessTest.Cycle?.PalletId ?? "미인식"} · {(WirelessTest.Cycle?.QrConfirmed==true?"UNO 확인 완료":"현재 회차 인식 대기")}":$"QR {SelectedOrder?.PalletId ?? "미배정"} · {Plant?.FeedQr?.State ?? "대기"} · 시도 {Plant?.FeedQr?.Attempt ?? 0}/3";
    public bool CanAccept => connected && !busy && selected?.FactoryStatus=="PENDING";
    public bool CanCancel => connected && !busy && selected is { CycleId:null } && selected.FactoryStatus is "PENDING" or "QUEUED";
    public bool CanStart => WirelessTest is {Enabled:true}?CanWirelessPing && WirelessTest is {SerialReady:true,Protocol:"UNO_SIM_QR_V2"} && WirelessTest.Cycle?.Stage!="UNKNOWN":connected && !busy && Plant?.State=="READY";
    public bool CanPause => WirelessTest is {Enabled:true} ? CanWirelessStop && !busy : connected && !busy && Plant?.State is "READY" or "RUNNING" or "NOT_READY";
    public bool CanResume => WirelessTest is not {Enabled:true} && connected && !busy && Plant?.State=="PAUSED";
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
    public DashboardViewModel(FactoryApiClient? client=null)
    {
        api=client ?? new();
        timer.Tick+=async(_,_)=>await RefreshAsync();
    }
    public async Task StartAsync()
    {
        Stop();
        var generation=pollingGeneration;
        await frameLoop;
        if(generation!=pollingGeneration)return;
        if(!Uri.TryCreate(ServerAddress,UriKind.Absolute,out var address) || address.Scheme is not ("http" or "https") || string.IsNullOrWhiteSpace(AdminToken))return;
        polling=new();var cancellation=polling.Token;timer.Start();await RefreshAsync();
        if(generation!=pollingGeneration || cancellation.IsCancellationRequested)return;
        frameLoop=ReadFramesAsync(cancellation);
    }
    public void Stop()
    {
        pollingGeneration++;timer.Stop();polling?.Cancel();polling?.Dispose();polling=null;connected=wirelessConnected=plantFresh=false;
        frameStamps.Clear();qrFrameCount=0;qrDisplayFps=0;frameClock.Restart();Notify(nameof(QrCameraTitle));NotifyAll();
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
            var failures=new List<string>();bool unauthorized=false;
            async Task<bool> Receive(string label,Func<Task> update)
            {
                try {await update();cancellation.ThrowIfCancellationRequested();return true;}
                catch(OperationCanceledException)when(cancellation.IsCancellationRequested){throw;}
                catch(Exception error)when(error is HttpRequestException or JsonException or IOException or OperationCanceledException)
                {
                    failures.Add(label+": "+error.Message);
                    if(label=="공정")plantFresh=false;
                    if(label=="무선 시험")wirelessConnected=false;
                    if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})
                    {unauthorized=true;connected=wirelessConnected=plantFresh=false;SessionExpired?.Invoke();}
                    return false;
                }
                finally {NotifyAll();}
            }
            void CheckSession(){cancellation.ThrowIfCancellationRequested();if(unauthorized)throw new OperationCanceledException(cancellation);}
            async Task<bool> ReadOrders()
            {
                return await Receive("주문",async()=>
                {
                    var orders=new List<AdminOrderV1>();string? cursor=null;
                    for(int i=0;i<pageCount;i++)
                    {
                        var page=await api.RequestAsync<OrderPageV1>(server,AdminToken,"orders?limit=50"+(cursor is null ? "":"&cursor="+Uri.EscapeDataString(cursor)),null,cancellation);
                        orders.AddRange(page.Orders);cursor=page.NextCursor;if(cursor is null)break;
                    }
                    CheckSession();var selectedId=selected?.OrderId;
                    Replace(Orders,orders);nextCursor=cursor;
                    SelectedOrder=Orders.FirstOrDefault(item=>item.OrderId==selectedId) ?? Orders.FirstOrDefault();
                });
            }
            // Each response updates its own list even when another endpoint fails.
            // All required snapshots must succeed in this round before process controls unlock.
            connected=false;
            var ordersTask=ReadOrders();
            var plantTask=Receive("공정",async()=>{var value=await api.RequestAsync<PlantV1>(server,AdminToken,"plant",null,cancellation);CheckSession();Plant=value;plantFresh=true;});
            var palletTask=Receive("팔레트",async()=>{var value=await api.RequestAsync<PalletPageV1>(server,AdminToken,"pallets?limit=100",null,cancellation);CheckSession();Replace(Pallets,value.Pallets);});
            var stockTask=Receive("재고",async()=>{var value=await api.RequestAsync<StockPageV1>(server,AdminToken,"stock?limit=100",null,cancellation);CheckSession();Replace(Stock,value.Items);});
            var wirelessTask=Receive("무선 시험",async()=>{var value=await api.RequestAsync<WirelessStatusV1>(server,AdminToken,"wireless-test/status",null,cancellation);CheckSession();WirelessTest=value;});
            var results=await Task.WhenAll(ordersTask,plantTask,palletTask,stockTask,wirelessTask);
            cancellation.ThrowIfCancellationRequested();
            if(unauthorized)return;
            connected=results.Take(4).All(ok=>ok);plantFresh=results[1];wirelessConnected=results[4];
            ConnectionStatus=failures.Count==0 ? $"서버·DB 연결 · {DateTime.Now:HH:mm:ss} 갱신 · 규약 0.1.0" : "일부 조회 실패 · 마지막 수신 상태 유지 · "+string.Join(" / ",failures);
            if(connected && ticketId is not null)
            {
                var ticket=await api.RequestAsync<TicketV1>(server,AdminToken,"plant/actions/"+ticketId,null,cancellation);
                ActionStatus=$"공정 요청 {ticket.State} · {ticket.PlantState}";
                if(ticket.State is "COMPLETED" or "FAILED" or "STOPPED")ticketId=null;
            }
            if(selected is not null && ++ticks%2==0)await Receive("주문 이력",()=>LoadHistoryAsync(server,cancellation));
            if(IsSuperAdmin && ticks%5==0)
            {
                await Receive("계정",async()=>{var page=await api.RequestAsync<AccountPageV1>(server,AdminToken,"accounts?limit=100",null,cancellation);CheckSession();Replace(Accounts,page.Accounts);});
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
    private async Task ReadFramesAsync(CancellationToken cancellation)
    {
        try
        {
            while(!cancellation.IsCancellationRequested)
            {
                frameAdvanced=false;await FramesAsync();
                // A single in-flight fetch continues as soon as it finishes. Waiting for
                // the next render tick would halve FPS when HTTP+decode just exceed 16ms.
                if(!frameAdvanced)await Task.Delay(connected?15:100,cancellation);
            }
        }
        catch(OperationCanceledException)when(cancellation.IsCancellationRequested){}
    }
    private async Task FramesAsync()
    {
        if(framesRefreshing || !plantFresh || polling is null)return;
        var cancellation=polling.Token;framesRefreshing=true;
        try
        {
            var server=new Uri(ServerAddress);
            // CAM3's display slot shows CAM1's ROI; physical CAM3 inspection APIs stay unchanged.
            var cameras=new[]{"cam1","cam2","cam1-roi","cam4"};
            var frames=await Task.WhenAll(cameras.Select(camera=>Plant?.CameraReady.GetValueOrDefault(camera=="cam1-roi"?"cam1":camera)!=true || IsTransportTest && camera=="cam2" ? Task.FromResult<CameraFrameV1?>(null) : camera=="cam1-roi" ? api.RoiFrameAsync(server,AdminToken,cancellation) : api.LiveFrameAsync(server,AdminToken,camera,cancellation)));
            cancellation.ThrowIfCancellationRequested();
            var changed=frames.Select((frame,index)=>frame is not null && (frame.CapturedAt is null || !frameStamps.TryGetValue(cameras[index],out var old) || old!=frame.CapturedAt)).ToArray();
            frameAdvanced=changed.Any(value=>value);
            var decoded=await Task.Run(()=>frames.Select((frame,index)=>changed[index]?Decode(frame!.Bytes):null).ToArray(),cancellation);
            cancellation.ThrowIfCancellationRequested();
            static ImageSource? Decode(byte[] bytes)
            {
                using var stream=new MemoryStream(bytes);var bitmap=new BitmapImage();
                bitmap.BeginInit();bitmap.CacheOption=BitmapCacheOption.OnLoad;bitmap.StreamSource=stream;bitmap.EndInit();bitmap.Freeze();return bitmap;
            }
            if(changed[0]){Cam1=decoded[0];qrFrameCount++;Notify(nameof(Cam1));}else if(frames[0] is null && Cam1 is not null){Cam1=null;Notify(nameof(Cam1));}
            if(changed[1]){Cam2=decoded[1];Notify(nameof(Cam2));}else if(frames[1] is null && Cam2 is not null){Cam2=null;Notify(nameof(Cam2));}
            if(changed[2]){Cam3=decoded[2];Notify(nameof(Cam3));}else if(frames[2] is null && Cam3 is not null){Cam3=null;Notify(nameof(Cam3));}
            if(changed[3]){Cam4=decoded[3];Notify(nameof(Cam4));}else if(frames[3] is null && Cam4 is not null){Cam4=null;Notify(nameof(Cam4));}
            for(var i=0;i<frames.Length;i++)if(changed[i])frameStamps[cameras[i]]=frames[i]!.CapturedAt;
            if(frameClock.Elapsed.TotalSeconds>=1){qrDisplayFps=qrFrameCount/frameClock.Elapsed.TotalSeconds;qrFrameCount=0;frameClock.Restart();Notify(nameof(QrCameraTitle));}
        }
        catch(OperationCanceledException)when(cancellation.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException or NotSupportedException or FormatException or COMException)
        {
            Cam1=Cam2=Cam3=Cam4=null;
            frameStamps.Clear();foreach(var name in new[]{nameof(Cam1),nameof(Cam2),nameof(Cam3),nameof(Cam4)})Notify(name);
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();
        }
        finally{framesRefreshing=false;}
    }
    private async Task ActAsync(string key,Func<Guid,CancellationToken,Task> action)
    {
        if(busy || polling is null || !(key.StartsWith("wireless:")?wirelessConnected:connected))return;
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
    public Task AcceptAsync()=>CanAccept && selected is { } order ? ActAsync("accept:"+order.OrderId,async(id,ct)=>{await api.RequestAsync<AdminOrderV1>(new Uri(ServerAddress),AdminToken,$"orders/{order.OrderId}/accept",new{request_id=id},ct);ActionStatus="제작 수락을 저장했습니다.";}):Task.CompletedTask;
    public Task CancelAsync()=>CanCancel && selected is { } order ? ActAsync("cancel:"+order.OrderId,async(id,ct)=>{await api.RequestAsync<AdminOrderV1>(new Uri(ServerAddress),AdminToken,$"orders/{order.OrderId}/cancel",new{request_id=id},ct);ActionStatus="주문 취소·재고 복구를 저장했습니다.";}):Task.CompletedTask;
    public Task PlantActionAsync(string action)=>WirelessTest is {Enabled:true} && action is "START" or "PAUSE" ? WirelessActionAsync(action=="START"?"RUN_CYCLE":"STOP") : ActAsync("plant:"+action,async(id,ct)=>{var ticket=await api.RequestAsync<TicketV1>(new Uri(ServerAddress),AdminToken,"plant/actions",new{request_id=id,action},ct);ticketId=ticket.RequestId;ActionStatus=$"요청 접수 {ticket.State} · 완료 대기";});
    public Task WirelessActionAsync(string action)=>!(action=="STOP"?CanWirelessStop:action=="RUN_CYCLE"?CanStart:action=="PING"?CanWirelessPing:action=="BELT"?CanWirelessBelt:CanWirelessMotion)?Task.CompletedTask:ActAsync("wireless:"+action,async(id,ct)=>
    {
        await api.RequestAsync<JsonElement>(new Uri(ServerAddress),AdminToken,"wireless-test/commands",new{request_id=id,action},ct);
        ActionStatus="무선 시험 명령 접수 · 아래 결과 이력에서 UNO 회신을 확인하세요.";
    });
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
        foreach(var name in new[]{nameof(StartLabel),nameof(HeaderDevices),nameof(ConveyorStatus),nameof(ConveyorDetail),nameof(ConveyorSteps)})Notify(name);
        foreach(var name in new[]{nameof(WirelessTest),nameof(WirelessSummary),nameof(WirelessTitle),nameof(CanWirelessPing),nameof(CanWirelessMotion),nameof(CanWirelessBelt),nameof(CanWirelessStop)})Notify(name);
        foreach(var name in new[]{nameof(IsTransportTest),nameof(AssemblyCameraTitle),nameof(InspectionCameraTitle),nameof(ProcessCaption)})Notify(name);
        foreach(var name in new[]{nameof(Plant),nameof(Summary),nameof(CycleInfo),nameof(EnvironmentLabel),nameof(ConnectionStatus),nameof(ActionStatus),nameof(Alert),nameof(Inspection),nameof(InspectionSummary),nameof(QrSummary),nameof(CanAccept),nameof(CanCancel),nameof(CanStart),nameof(CanPause),nameof(CanResume),nameof(CanReadQr),nameof(CanSaveQr),nameof(CanLoadMore),nameof(CanAdminister),nameof(OrderCount),nameof(PalletCount)})Notify(name);
        Notify(nameof(CanReconcile));Notify(nameof(CanViewImage));Notify(nameof(CanDeleteImage));
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName]string? name=null)=>PropertyChanged?.Invoke(this,new(name));
}
