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
public sealed record TrialDestination(int Station,string Label,string Address);

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
    private bool refreshing,framesRefreshing,busy,connected,plantFresh;
    private int pageCount=1,ticks;
    private string? nextCursor,ticketId;
    private sealed class QrRetry(Guid requestId,Guid cycleId,Guid previousReadId,string action)
    {
        public Guid RequestId {get;}=requestId;
        public Guid CycleId {get;}=cycleId;
        public Guid PreviousReadId {get;}=previousReadId;
        public string Action {get;}=action;
        public Guid? ReadId {get;private set;}
        public bool InFlight {get;set;}
        public string Path=>$"cycles/{CycleId}/qr-retry";

        public void CheckTicket(QrTicketV1 ticket)
        {
            if(ticket.RequestId!=RequestId || ticket.CycleId!=CycleId || ticket.ReadId is not Guid read ||
                (ReadId.HasValue && ReadId!=read) || (Action=="SAVE_BINDING")!=(read==PreviousReadId))
                throw new JsonException("QR 재시도 응답의 요청·회차·판독 묶음이 일치하지 않습니다.");
            ReadId=read;
        }
    }
    private QrRetry? pendingQr,retryAfterNotFound;
    private string? qrActionStatus;
    private string actionStatus="제작 수락 후 전체 준비 상태를 확인하세요.";
    private string? completedConveyorCycle;
    private readonly Dictionary<string,Guid> requestIds=[];
    private sealed class MixedRequest(TrialDestination destination)
    {
        public TrialDestination Destination {get;}=destination;
        public Guid SubmitId {get;}=Guid.NewGuid();
        public Guid AcceptId {get;}=Guid.NewGuid();
        public Guid StartId {get;}=Guid.NewGuid();
    }
    private MixedRequest? mixedRequest;
    private Guid? recoveryRequest,recoveryCommand;
    private string? recoveryReason;
    public bool StandbyConfirmed {get;set;}
    public bool CanRecoverStandby=>IsMixedTrial && connected && !busy && Plant?.CycleId is null &&
        Plant?.State is "READY" or "NOT_READY" or "RECOVERY_REQUIRED" &&
        Plant.Devices.Any(d=>d.DeviceId=="LINE" && d.Online && d.State=="IDLE" && d.ActiveCommandId is null);
    public IReadOnlyList<TrialDestination> TrialDestinations {get;}=[new(3,"3번 서울 HUB","서울특별시 중구 시험"),new(4,"4번 대전 HUB","대전광역시 유성구 시험"),new(5,"5번 천안 HUB","충청남도 천안시 시험")];
    public TrialDestination? SelectedTrialDestination {get;set;}
    public bool IsMixedTrial=>api.TrialProfile=="LINE_AGV_MIXED_V1";
    public bool CanRunMixedTrial=>IsMixedTrial && connected && !busy && (mixedRequest is not null ||
        Plant is {State:"READY",CycleId:null} && !Orders.Any(o=>o.FactoryStatus is "QUEUED" or "IN_PROGRESS"));
    public bool CanSelectTrialDestination=>IsMixedTrial && !busy && mixedRequest is null;
    public string MixedTrialButtonLabel=>mixedRequest is null ? "선택 목적지로 실장비 시험 시작" : "같은 시험 요청 접수 확인";
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
    public InspectionV1? Inspection {get;private set;}
    public ImageSource? Cam1 {get;private set;}
    public ImageSource? Cam2 {get;private set;}
    public ImageSource? Cam3 {get;private set;}
    public ImageSource? Cam4 {get;private set;}
    public bool CameraTabVisible {get;set;}=true;
    public string ConnectionStatus {get;private set;}="로그인 후 연결합니다.";
    public string ActionStatus {get=>qrActionStatus is null ? actionStatus : $"{actionStatus}\n{qrActionStatus}";private set=>actionStatus=value;}
    private bool IsSimulation => api.Environment=="SIMULATION";
    public bool IsConveyorTrial { get; init; }
    public string EnvironmentLabel => IsMixedTrial ? "실장비 혼합시험 · LINE/AGV 실물 · 조립/YOLO 모의" : IsConveyorTrial ? "컨베이어 시험 · 시험 주문" :
        IsSimulation ? "모의 검증 · 실물 장비 미사용" : "실물 모드 · 준비·완료 증거 확인";
    public string StartButtonText => IsConveyorTrial ? "처음부터 시작" : "자동 운전 시작";
    public string QrPanelTitle => "CAM 01 · QR 판독 ROI";
    private const string QrLocation = "CAM1 QR";
    public string AssemblyCameraTitle=>"CAM 02 · 조립 위치 보정";
    public string QrCameraTitle=>$"CAM 01 · 전체 영상 · {qrDisplayFps:0} fps · 90°";
    public string InspectionCameraTitle=>"CAM 03 · YOLO 검사 영상";
    public string ProcessCaption => IsConveyorTrial ?
        "팔레트 배출 → IR1 감지 후 5.80초 이송 → CAM1 정차·QR 배정 → 모의 조립 3초 → IR2 감지 후 1.75초 이송·정차" :
        "규약 0.3.0 · IR1 정차 후 QR 배정 · 조립/검사 합격 후 IR2 이송 · AGV 하역 및 복귀";
    public IReadOnlyList<DeviceV1> HeaderDevices =>
        IsConveyorTrial ? Plant?.Devices.Where(device => device.DeviceId == "LINE").ToArray() ?? [] : Plant?.Devices ?? [];
    public string ProcessDetail => Plant is { } plant
        ? $"공정 상태 {plant.UpdatedAt.ToLocalTime():HH:mm:ss}{(plantFresh ? "" : " · 마지막 수신")}"
        : "공정 상태 대기";
    private static readonly string[] ConveyorLabels =
        ["팔레트 배출", "QR 인식", "조립/검사", "AGV 인수", "AGV 하역 완료"];
    private static readonly string[] TrialLabels =
        ["팔레트 배출", "IR1 · 카메라 이송", "CAM1 QR·DB 배정", "모의 조립 3초", "IR2 이송·정차", "시험 완료"];

    private bool AgvPickupAccepted => Plant?.CycleId is string cycle &&
        Plant.Devices.Any(device => device.DeviceId == "AGV" && device.CycleId == cycle && device.ActiveCommandId != null);

    private bool ShowingLastCompletion => plantFresh && Plant is { State:"READY", CycleId:null }
        && completedConveyorCycle is not null;

    private int ConveyorStepIndex => !plantFresh ? -1 : Plant?.Stage switch
    {
        "DISPENSING" => 0,
        "WAIT_FEED_QR" => IsConveyorTrial ? 2 : 1,
        "TO_ASSEMBLY" => 1,
        "ASSEMBLING" or "INSPECTING" => IsConveyorTrial ? 3 : 2,
        "TO_PICKUP" => IsConveyorTrial ? 4 : 2,
        "PICKING_UP" => IsConveyorTrial ? 5 : AgvPickupAccepted ? 3 : 2,
        "DELIVERING" or "UNLOADING" or "TO_INSPECTION" => 3,
        "RETURNING" or "CLOSED" => IsConveyorTrial ? TrialLabels.Length : ConveyorLabels.Length,
        _ when ShowingLastCompletion => IsConveyorTrial ? TrialLabels.Length : ConveyorLabels.Length,
        _ => -1
    };

    public IReadOnlyList<ConveyorStep> ProcessSteps
    {
        get
        {
            var step=ConveyorStepIndex;
            var labels = IsConveyorTrial ? TrialLabels : ConveyorLabels;
            bool inDelivery=plantFresh && !IsConveyorTrial && Plant?.Stage is ("DELIVERING" or "UNLOADING");
            return labels.Select((label,index)=>new ConveyorStep(label,index==step && !inDelivery,
                index<step || (inDelivery && index==3))).ToArray();
        }
    }

    public string Summary
    {
        get
        {
            if(!plantFresh || Plant is not { } plant)return "공정 상태 연결 대기";
            if (IsConveyorTrial && plant is { Stage: "CLOSED", State:"READY" })
                return "IR2 정차 완료 · 다음 회차 시작 가능";
            if (IsConveyorTrial && plant is { Stage: "PICKING_UP", State: "PAUSED" })
                return "IR2 정차 · 회차 기록 저장 중";
            return plant.State switch
            {
                "NOT_READY" => "설비 준비 대기",
                "PAUSING" => "장비 정지 확인 중",
                "PAUSED" => "공정 일시정지",
                "RESUMING" => "정지 해제 확인 중",
                "RECOVERY_REQUIRED" => "현장 확인 필요",
                _ => plant.Stage switch
                {
                    "DISPENSING" => "팔레트 배출 확인 중",
                    "WAIT_FEED_QR" => plant.FeedQr?.State switch
                    {
                        "UNREADABLE" => $"{QrLocation} 재판독 대기",
                        "SAVE_FAILED" => "DB 배정 저장 재시도 대기",
                        _ => $"{QrLocation} 판독·DB 배정 중"
                    },
                    "TO_ASSEMBLY" => IsConveyorTrial ? "IR1 감지 후 카메라 위치까지 이송" : "IR1까지 이송 · 정차 확인 대기",
                    "ASSEMBLING" => IsConveyorTrial ? "QR 배정 완료 · 모의 조립 3초 정차" :
                        IsSimulation ? "로봇팔 모의 조립 중" : "로봇팔 조립·대기 자세 확인 중",
                    "TO_PICKUP" => "IR2까지 이송 · 정차 확인 대기",
                    "PICKING_UP" => IsConveyorTrial ? "IR2 도착 · 최종 정지 응답 대기" :
                        AgvPickupAccepted ? "AGV 인수 중" : "IR2 정차 · AGV 명령 접수 대기",
                    "INSPECTING" => Inspection?.CycleId==plant.CycleId && Inspection?.Verdict is not (null or "PASS")
                        ? "검사 결과 확인 필요 · 정차 유지" : "조립 위치 정차 · YOLO 검사",
                    "TO_INSPECTION" => "이전 회차의 검사 이력",
                    "DELIVERING" or "UNLOADING" => "AGV 배송·하역 중",
                    "RETURNING" => "AGV 하역 완료 · 대기 위치 복귀 중",
                    _ => ShowingLastCompletion ? "AGV 하역 완료 · 다음 작업 대기" : "자동 운전 대기"
                }
            };
        }
    }

    public string CycleInfo => Plant?.CycleId is {} cycle ? $"회차 {cycle}"
        : ShowingLastCompletion ? $"완료한 컨베이어 회차 {completedConveyorCycle}"
        : "회차 대기";
    public string OrderCount => $"주문 {Orders.Count}건";
    public string PalletCount => $"팔레트 {Pallets.Count}개";
    public string Alert => !connected ? "서버 연결 끊김 · 마지막 수신 상태 표시 · 조작 차단" :
        Plant?.State=="RECOVERY_REQUIRED" ? "결과 미확인 · 상태와 실물을 대조한 후 현장 확인이 필요합니다." :
        Plant?.State=="PAUSING" ? "정지 확인 중 · 장비별 연결 및 정지 응답을 확인하세요." :
        Plant?.FeedQr?.State=="UNREADABLE" ? $"{QrLocation} 3회 실패 · 벨트 정차 · 재판독이 필요합니다." :
        Plant?.FeedQr?.State=="SAVE_FAILED" ? "QR 판독 성공 · DB 저장 실패 · 저장만 재시도하세요." :
        Plant?.State=="NOT_READY" ? "상단에서 장비 연결을 확인하세요. 카메라와 구동 설정도 준비되어야 시작할 수 있습니다." :
        Plant?.Stage=="INSPECTING" && Inspection?.CycleId==Plant.CycleId && Inspection?.Verdict is not (null or "PASS")
            ? "검사 결과 확인 필요 · 벨트 정차 유지" : "현재 공정 경고 없음";
    public string InspectionSummary => Inspection is null ? "검사 결과 대기" :
        $"{(Inspection.AttemptNumber is int attempt ? $"{attempt}차 검사 · " : "")}{Inspection.Verdict}" +
        $"{(Inspection.DispatchStation is int station ? $" · {HubNames.Label(station)}" : "")}\n{string.Join(", ",Inspection.Reasons)}";
    public string StartUnavailableReason => CanStart ? "" : !connected ? "서버·DB 상태 확인 대기" :
        Plant?.State=="RECOVERY_REQUIRED" ? "현장 확인 필요" : Plant?.State=="NOT_READY" ?
        "장비·카메라·보정 준비 확인" : "현재 회차 또는 정지 상태 확인";
    public string QrSummary
    {
        get
        {
            if(!plantFresh)return $"{QrLocation} · 공정 상태 확인 대기";
            if(Plant?.CycleId is not string cycle)return $"{QrLocation} · 회차 대기";
            if(Plant.FeedQr is not {} qr)return $"현재 회차 · {QrLocation} 판독 대기";
            if(qr.State=="BOUND")
            {
                var pallet=Pallets.FirstOrDefault(item=>item.CycleId==cycle);
                return $"현재 회차 · DB 배정 완료 · {pallet?.PalletId ?? "팔레트 번호 조회 대기"}";
            }
            var status=qr.State switch
            {
                "READING"=>"판독 중", "UNREADABLE"=>"판독 실패", "SAVING"=>"DB 배정 중",
                "SAVE_FAILED"=>"DB 배정 저장 실패", _=>qr.State
            };
            return $"현재 회차 · {QrLocation} {status} · 시도 {qr.Attempt}/3";
        }
    }
    public bool CanAccept => connected && !busy && selected?.FactoryStatus=="PENDING";
    public bool CanCancel => connected && !busy && selected is { CycleId:null } && selected.FactoryStatus is "PENDING" or "QUEUED";
    public bool CanStart => connected && !busy && Plant?.State=="READY";
    public bool CanPause => plantFresh && !busy && Plant?.State is "READY" or "RUNNING" or "NOT_READY";
    public bool CanResume => connected && !busy && Plant?.State=="PAUSED" && !(IsConveyorTrial && Plant.Stage=="PICKING_UP");
    private bool CanRetryQr => pendingQr is null && connected && !busy && Plant is {State:"RUNNING",Stage:"WAIT_FEED_QR",CycleId:not null};
    public bool CanReadQr => CanRetryQr && Plant?.FeedQr?.State=="UNREADABLE";
    public bool CanSaveQr => CanRetryQr && Plant?.FeedQr?.State=="SAVE_FAILED";
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
        SelectedTrialDestination=TrialDestinations[0];
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
        pollingGeneration++;timer.Stop();polling?.Cancel();polling?.Dispose();polling=null;connected=plantFresh=false;completedConveyorCycle=null;
        pendingQr=retryAfterNotFound=null;qrActionStatus=null;
        frameStamps.Clear();qrFrameCount=0;qrDisplayFps=0;frameClock.Restart();Notify(nameof(QrCameraTitle));NotifyAll();
    }
    public void ClearSession()
    {
        Stop();AdminToken="";Orders.Clear();Pallets.Clear();Stock.Clear();History.Clear();Accounts.Clear();SelectedOrder=null;
        Cam1=Cam2=Cam3=Cam4=null;Plant=null;requestIds.Clear();mixedRequest=null;recoveryRequest=recoveryCommand=null;recoveryReason=null;StandbyConfirmed=false;NotifyAll();
    }
    private static void Replace<T>(ObservableCollection<T> target,IEnumerable<T> values)
    {
        var array=values.ToArray();if(target.SequenceEqual(array))return;
        target.Clear();foreach(var item in array)target.Add(item);
    }
    private void UpdatePlant(PlantV1 plant)
    {
        Plant=plant;
        plantFresh=true;
        if(plant.CycleId is {} cycle)
            completedConveyorCycle=plant.Stage is "RETURNING" or "CLOSED" ? cycle : null;
    }

    public async Task RefreshAsync()
    {
        if(polling is null)return;
        var cancellation=polling.Token;
        await Task.WhenAll(RefreshSnapshotsAsync(),CheckQrRetryAsync(cancellation));
        await CheckStandbyRecoveryAsync(cancellation);
    }

    private async Task RefreshSnapshotsAsync()
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
                    if(label=="공정"){plantFresh=false;completedConveyorCycle=null;}
                    if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})
                    {unauthorized=true;connected=plantFresh=false;SessionExpired?.Invoke();}
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
            var plantTask=Receive("공정",async()=>{var value=await api.RequestAsync<PlantV1>(server,AdminToken,"plant",null,cancellation);CheckSession();UpdatePlant(value);});
            var palletTask=Receive("팔레트",async()=>{var value=await api.RequestAsync<PalletPageV1>(server,AdminToken,"pallets?limit=100",null,cancellation);CheckSession();Replace(Pallets,value.Pallets);});
            var stockTask=Receive("재고",async()=>{var value=await api.RequestAsync<StockPageV1>(server,AdminToken,"stock?limit=100",null,cancellation);CheckSession();Replace(Stock,value.Items);});
            var results=await Task.WhenAll(ordersTask,plantTask,palletTask,stockTask);
            cancellation.ThrowIfCancellationRequested();
            if(unauthorized)return;
            connected=results.All(ok=>ok);plantFresh=results[1];
            ConnectionStatus=failures.Count==0 ? $"서버·DB 연결 · {DateTime.Now:HH:mm:ss} 갱신" : "일부 조회 실패 · 마지막 수신 상태 유지 · "+string.Join(" / ",failures);
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
            var cameras=new[]{"cam1","cam2","cam3","cam4"};
            var frames=await Task.WhenAll(cameras.Select(camera=>
                (camera=="cam4" || CameraTabVisible) && Plant?.CameraReady.GetValueOrDefault(camera)==true
                    ? api.LiveFrameAsync(server,AdminToken,camera,cancellation) : Task.FromResult<CameraFrameV1?>(null)));
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
            for(var i=0;i<frames.Length;i++)
            {
                if(frames[i] is null)frameStamps.Remove(cameras[i]);
                else if(changed[i])frameStamps[cameras[i]]=frames[i]!.CapturedAt;
            }
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
    private async Task ActAsync(string key,Func<Guid,CancellationToken,Task> action,bool plantOnly=false)
    {
        if(busy || polling is null || !(plantOnly?plantFresh:connected))return;
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
    public async Task RecoverStandbyAsync()
    {
        if(!CanRecoverStandby || polling is null)return;
        if(recoveryCommand is not null){await CheckStandbyRecoveryAsync(polling.Token);return;}
        if(!StandbyConfirmed){ActionStatus="AGV 실제 2번·무적재·컨베이어와 액추에이터 정지·컨베이어 비움을 확인하고 체크하세요.";NotifyAll();return;}
        recoveryRequest ??= Guid.NewGuid();
        recoveryReason ??= "작업자 확인: AGV 실제 2번·무적재, 컨베이어·액추에이터 모두 정지, 컨베이어 위 팔레트 없음. 활성 주문 없이 시험 대기 상태 복구 요청.";
        var ct=polling.Token;busy=true;NotifyAll();
        try
        {
            var result=await api.RequestAsync<ManualResultV1>(new Uri(ServerAddress),AdminToken,"manual-actions",
                new{request_id=recoveryRequest,device_id="AGV",procedure_id="AGV_IDLE_STANDBY_RECOVERY_V1",reason=recoveryReason},ct);
            ct.ThrowIfCancellationRequested();recoveryCommand=result.CommandId;
            ActionStatus="대기 복구 접수 · 장치 상태 대조 중 · "+result.CommandId;
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {ActionStatus="대기 복구 확인 필요 · 같은 요청으로 다시 확인 · "+error.Message;if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();}
        finally{busy=false;NotifyAll();}
        await RefreshAsync();
    }
    private async Task CheckStandbyRecoveryAsync(CancellationToken ct)
    {
        if(recoveryCommand is not Guid command)return;
        try
        {
            var result=await api.RequestAsync<ManualResultV1>(new Uri(ServerAddress),AdminToken,$"manual-actions/{command}",null,ct);
            if(result.CommandId!=command)throw new JsonException("대기 복구 명령 ID가 다릅니다.");
            ActionStatus=result.State switch {
                "COMPLETED"=>"AGV 2번·지게 85° 대기 복구 완료 · 자동운전은 꺼진 상태입니다.",
                "UNKNOWN"=>"복구 결과 미확정 · 현장 확인 필요 · "+command,
                "FAILED" or "STOPPED"=>"복구 중단 · 현장 확인 필요 · "+command,
                _=>"대기 복구 진행 중 · "+command};
            if(result.State is "COMPLETED" or "FAILED" or "STOPPED" or "UNKNOWN")
            {recoveryRequest=recoveryCommand=null;recoveryReason=null;StandbyConfirmed=false;}
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {ActionStatus="대기 복구 조회 필요 · "+error.Message;if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();}
        finally{NotifyAll();}
    }
    public async Task StartMixedTrialAsync()
    {
        if(!CanRunMixedTrial || polling is null || SelectedTrialDestination is null)return;
        mixedRequest ??= new(SelectedTrialDestination);
        var trial=mixedRequest;
        var ct=polling.Token;busy=true;NotifyAll();
        try
        {
            var server=new Uri(ServerAddress);
            var order=await api.RequestAsync<JsonElement>(server,AdminToken,"orders",new{
                request_id=trial.SubmitId,company="실장비 혼합시험",phone="010-0000-0000",address=trial.Destination.Address,
                spec=new{target_head_id="H1",target_arm_l_id="L1",target_arm_r_id="R2"},payment_method="card"},ct);
            var orderId=order.GetProperty("order_id").GetString();
            if(!Guid.TryParse(orderId,out _) || order.GetProperty("target_station").GetInt32()!=trial.Destination.Station)
                throw new JsonException("시험 주문 ID 또는 목적지가 요청과 다릅니다.");
            await api.RequestAsync<AdminOrderV1>(server,AdminToken,$"orders/{orderId}/accept",new{request_id=trial.AcceptId},ct);
            var ticket=await api.RequestAsync<TicketV1>(server,AdminToken,"plant/actions",new{request_id=trial.StartId,action="START",reason="LINE/AGV 실물 · 조립/검사 모의 시험"},ct);
            ct.ThrowIfCancellationRequested();ticketId=ticket.RequestId;mixedRequest=null;
            ActionStatus=$"{trial.Destination.Label} 시험 접수 · 실제 완료 대기 · 조립/검사 모의";
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {
            ActionStatus="시험 요청 결과 확인 필요 · 같은 요청으로 접수 확인 · "+error.Message;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})SessionExpired?.Invoke();
        }
        finally{busy=false;NotifyAll();}
        await RefreshAsync();
    }
    public Task CancelAsync()=>CanCancel && selected is { } order ? ActAsync("cancel:"+order.OrderId,async(id,ct)=>{await api.RequestAsync<AdminOrderV1>(new Uri(ServerAddress),AdminToken,$"orders/{order.OrderId}/cancel",new{request_id=id},ct);ActionStatus="주문 취소·재고 복구를 저장했습니다.";}):Task.CompletedTask;
    public Task PlantActionAsync(string action)
    {
        var permitted=action switch { "START"=>CanStart, "PAUSE"=>CanPause, "RESUME"=>CanResume, _=>false };
        if(!permitted)return Task.CompletedTask;
        return ActAsync("plant:"+action,async(id,ct)=>
        {
            var ticket=await api.RequestAsync<TicketV1>(new Uri(ServerAddress),AdminToken,"plant/actions",new{request_id=id,action},ct);
            ticketId=ticket.RequestId;
            ActionStatus=$"요청 접수 {ticket.State} · 완료 대기";
        },plantOnly:action=="PAUSE");
    }
    public async Task RetryQrAsync(string action)
    {
        if(!(action switch { "READ_QR"=>CanReadQr, "SAVE_BINDING"=>CanSaveQr, _=>false }))return;
        if(polling is null || !Guid.TryParse(Plant?.CycleId,out var cycle) || !Guid.TryParse(Plant?.FeedQr?.ReadId,out var previousRead))return;
        var ct=polling.Token;
        var prior=retryAfterNotFound;
        var retry=prior is not null && prior.CycleId==cycle && prior.PreviousReadId==previousRead && prior.Action==action
            ? prior : new QrRetry(Guid.NewGuid(),cycle,previousRead,action);
        pendingQr=retry;retryAfterNotFound=null;retry.InFlight=true;
        qrActionStatus="QR 재시도 요청 중";NotifyAll();
        try
        {
            var ticket=await api.RequestAsync<QrTicketV1>(new Uri(ServerAddress),AdminToken,retry.Path,
                new{request_id=retry.RequestId,action,previous_read_id=previousRead},ct);
            ct.ThrowIfCancellationRequested();
            retry.CheckTicket(ticket);
            qrActionStatus="QR 재시도 접수 · 결과 조회 중";
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){return;}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {
            if(ct.IsCancellationRequested)return;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})
            {pendingQr=null;SessionExpired?.Invoke();return;}
            if(error is HttpRequestException{StatusCode:HttpStatusCode.BadRequest or HttpStatusCode.Forbidden or HttpStatusCode.NotFound or HttpStatusCode.Conflict or HttpStatusCode.UnprocessableEntity})
            {pendingQr=null;qrActionStatus="QR 재시도 거부 · "+error.Message;return;}
            qrActionStatus="QR 재시도 접수 여부 확인 중 · "+error.Message;
        }
        finally{retry.InFlight=false;NotifyAll();}
        await RefreshAsync();
    }

    private async Task CheckQrRetryAsync(CancellationToken ct)
    {
        if(pendingQr is not {} retry || retry.InFlight)return;
        retry.InFlight=true;
        try
        {
            var ticket=await api.RequestAsync<QrTicketV1>(new Uri(ServerAddress),AdminToken,
                $"{retry.Path}/{retry.RequestId}",null,ct);
            ct.ThrowIfCancellationRequested();
            retry.CheckTicket(ticket);
            qrActionStatus="QR 재시도 "+(ticket.State switch {
                "COMPLETED"=>"완료", "FAILED"=>"실패", "STOPPED"=>"정지",
                "UNKNOWN"=>"결과 미확인 · 상태 조회 중", "RUNNING"=>"진행 중", _=>"접수 · 결과 조회 중"});
            if(ticket.State is "COMPLETED" or "FAILED" or "STOPPED")pendingQr=null;
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){}
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
        {
            if(ct.IsCancellationRequested)return;
            if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})
            {pendingQr=null;SessionExpired?.Invoke();return;}
            if(retry.ReadId is null && error is HttpRequestException{StatusCode:HttpStatusCode.NotFound})
            {
                pendingQr=null;retryAfterNotFound=retry;
                qrActionStatus="QR 재시도 접수 기록 없음 · 같은 회차·판독 묶음은 같은 요청 번호로 다시 시도할 수 있습니다.";
            }
            else qrActionStatus="QR 재시도 결과 미확인 · 상태 재조회 중 · "+error.Message;
        }
        finally{retry.InFlight=false;NotifyAll();}
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
        foreach(var name in new[]{nameof(IsMixedTrial),nameof(CanRunMixedTrial),nameof(CanSelectTrialDestination),nameof(MixedTrialButtonLabel),nameof(CanRecoverStandby),nameof(StandbyConfirmed)})Notify(name);
        foreach(var name in new[]{nameof(HeaderDevices),nameof(ProcessDetail),nameof(ProcessSteps)})Notify(name);
        Notify(nameof(StartUnavailableReason));
        foreach(var name in new[]{nameof(AssemblyCameraTitle),nameof(InspectionCameraTitle),nameof(ProcessCaption)})Notify(name);
        foreach(var name in new[]{nameof(Plant),nameof(Summary),nameof(CycleInfo),nameof(EnvironmentLabel),nameof(ConnectionStatus),nameof(ActionStatus),nameof(Alert),nameof(Inspection),nameof(InspectionSummary),nameof(QrSummary),nameof(CanAccept),nameof(CanCancel),nameof(CanStart),nameof(CanPause),nameof(CanResume),nameof(CanReadQr),nameof(CanSaveQr),nameof(CanLoadMore),nameof(CanAdminister),nameof(OrderCount),nameof(PalletCount)})Notify(name);
        Notify(nameof(CanReconcile));Notify(nameof(CanViewImage));Notify(nameof(CanDeleteImage));
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName]string? name=null)=>PropertyChanged?.Invoke(this,new(name));
}
