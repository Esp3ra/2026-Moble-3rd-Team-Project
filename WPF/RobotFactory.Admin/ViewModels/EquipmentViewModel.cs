using System.Collections.ObjectModel;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed partial class DashboardViewModel
{
    private readonly EquipmentApiClient equipmentApi = new();
    private readonly DispatcherTimer frameTimer = new() { Interval = TimeSpan.FromMilliseconds(200) };
    private int equipmentTicks;
    private bool equipmentConnected;
    private bool equipmentRefreshing;
    private bool frameRefreshing;
    private bool equipmentBusy;
    private string equipmentError = "";
    private string demoPalletId = "";
    private bool positionReady;
    private Guid? startRequestId;
    private string? requestJob;
    private string? requestPallet;
    public EquipmentState Equipment { get; private set; } = new();
    public ImageSource? CameraFrame { get; private set; }
    public string CameraStatus { get; private set; } = "데모 영상 연결 대기";
    public ObservableCollection<EventPreview> Events { get; } = [];
    public string Ir1DelayMs { get; set; } = "5500";
    public string RobotWaitMs { get; set; } = "3000";
    public string Ir2DelayMs { get; set; } = "2000";
    public string DemoPalletId { get => demoPalletId; set { demoPalletId = value; Notify(); NotifyEquipment(); } }
    public bool PositionReady { get => positionReady; set { positionReady = value; Notify(); NotifyEquipment(); } }
    public bool CanConfigureEquipment => equipmentConnected && Equipment.CanStart && !equipmentBusy;
    public bool CanStartEquipment => CanConfigureEquipment && PositionReady && SelectedOrder?.FactoryStatus == "PENDING" &&
        Pallets.Any(pallet => pallet.PalletId == DemoPalletId && pallet.CurrentStatus == "READY");
    public bool CanStopEquipment => equipmentConnected && !equipmentBusy && Equipment.Run is { FinishedAt: null };
    public bool CanConfirmEquipment => equipmentConnected && !equipmentBusy && Equipment.CanConfirm;
    public bool CanRetryEquipment => equipmentConnected && !equipmentBusy && Equipment.CanRetry;
    public string EquipmentSummary => equipmentConnected
        ? $"{EquipmentLabel} · {StateLabel(Equipment.State)} · {Equipment.Run?.JobId ?? "주문 선택 대기"}"
        : "UNO DEMO 연결 대기";
    public string EquipmentSensorStatus => equipmentConnected
        ? $"IR1 {(Equipment.IrClear ? "비어 있음" : "감지")} · IR2 {(Equipment.Ir2Clear ? "비어 있음" : "감지")}" : "IR 센서 상태 수신 대기";
    public string EquipmentRunInfo => Equipment.Run is { } run
        ? $"회차 {run.RunId}\n주문 {run.JobId} · DB {run.DatabaseStatus}\n배정 {run.MappingId?.ToString() ?? "대기"} · 팔레트 {run.PalletId ?? "QR 대기"}"
        : "선택한 주문으로 장비 없는 데모를 실행합니다.";
    public string EquipmentAlert => !string.IsNullOrEmpty(equipmentError) ? equipmentError :
        !string.IsNullOrEmpty(Equipment.Error) ? Equipment.Error :
        !string.IsNullOrEmpty(Equipment.Run?.DatabaseError) ? Equipment.Run.DatabaseError : "현재 장비 알림 없음";
    public string EquipmentLabel => Equipment.Mode == "REMOTE" ? $"원격 UNO · {Equipment.HardwareMode}" : "UNO DEMO";
    public string EquipmentNotice => Equipment.Mode == "REMOTE" ? $"라즈베리파이 원격 연결 · {Equipment.HardwareMode}" : "DEMO · 실제 USB·카메라·모터 미연결";
    public string AppliedTimings => Equipment.Timings.Values.Count == 0 ? "시간 설정 조회 대기" :
        $"UNO 적용 {Equipment.Timings.Values.GetValueOrDefault("ir1_delay_ms")} / {Equipment.Timings.Values.GetValueOrDefault("robot_wait_ms")} / {Equipment.Timings.Values.GetValueOrDefault("ir2_delay_ms")} ms · {Equipment.Timings.Status}";

    private static string StateLabel(string state) => state switch
    {
        "IDLE" => "대기", "STARTING" => "시작 확인", "PUSHING" => "팔레트 배출",
        "RETURNING" => "복귀·컨베이어 운전", "WAIT_IR" => "IR1 지연 운전",
        "ROBOT_WAIT" => "작업 가정 대기", "SECOND_BELT" => "컨베이어 재구동",
        "WAIT_IR2" => "IR2 지연 운전", "AWAIT_CONFIRM" => "배출 확인 대기",
        "SUCCESS" => "장비 회차 성공", "STOPPED" => "정지", "ERROR" => "오류",
        "QR_UNCONFIRMED" => "QR 미확인", _ => state
    };

    private void InitializeEquipmentPolling() => frameTimer.Tick += async (_, _) => await RefreshFrameAsync();

    private async Task RefreshEquipmentAsync()
    {
        if (equipmentRefreshing || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        equipmentRefreshing = true;
        try
        {
            if (Equipment.IsOwner && Equipment.Run is { FinishedAt: null })
            {
                try { await equipmentApi.HeartbeatAsync(connectedServer, connectedToken, cancellation); }
                catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.Conflict) { }
            }
            var state = await equipmentApi.GetStateAsync(connectedServer, connectedToken, cancellation);
            cancellation.ThrowIfCancellationRequested();
            ApplyEquipment(state);
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception error) when (error is HttpRequestException or JsonException or IOException or OperationCanceledException)
        {
            equipmentConnected = false;
            equipmentError = error is OperationCanceledException ? "장비 서버 응답 시간 초과" : error.Message;
            NotifyEquipment();
            if (error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally { equipmentRefreshing = false; }
    }

    private async Task RefreshFrameAsync()
    {
        if (frameRefreshing || !equipmentConnected || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        frameRefreshing = true;
        try
        {
            var bytes = await equipmentApi.GetFrameAsync(connectedServer, connectedToken, cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (bytes is null || bytes.Length == 0) throw new IOException("데모 영상 데이터가 없습니다.");
            using var stream = new MemoryStream(bytes);
            var image = new BitmapImage();
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
            CameraFrame = image;
            CameraStatus = Equipment.Mode == "REMOTE" ? $"라즈베리파이 영상 · {Equipment.HardwareMode}" : "UNO DEMO · 합성 영상 / 실제 QR 이미지 판독";
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return; }
        catch (Exception error) when (error is HttpRequestException or IOException or JsonException or OperationCanceledException or NotSupportedException or FormatException)
        {
            CameraFrame = null;
            CameraStatus = "영상 수신 실패 · " + error.Message;
            if (error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally
        {
            frameRefreshing = false;
            Notify(nameof(CameraFrame));
            Notify(nameof(CameraStatus));
        }
    }

    private void ApplyEquipment(EquipmentState state)
    {
        Equipment = state;
        equipmentConnected = true;
        equipmentError = "";
        Events.Clear();
        foreach (var item in state.Events.TakeLast(3))
        {
            string time = DateTimeOffset.TryParse(item.At, out var at) ? at.ToLocalTime().ToString("HH:mm:ss") : "";
            Events.Add(new EventPreview(time, string.IsNullOrEmpty(item.Text) ? item.Name : $"{item.Name} · {item.Text}"));
        }
        NotifyEquipment();
    }

    private void NotifyEquipment()
    {
        foreach (var name in new[] { nameof(Equipment), nameof(EquipmentSummary), nameof(EquipmentSensorStatus),
            nameof(EquipmentRunInfo), nameof(EquipmentAlert), nameof(AppliedTimings), nameof(CanConfigureEquipment),
            nameof(CanStartEquipment), nameof(CanStopEquipment), nameof(CanConfirmEquipment), nameof(CanRetryEquipment),
            nameof(ExpectedQr), nameof(ScannedQr), nameof(QrMatchState), nameof(Connections), nameof(CanDropOrder) }) Notify(name);
        Notify(nameof(EquipmentLabel)); Notify(nameof(EquipmentNotice));
    }

    private async Task EquipmentActionAsync(string action, object payload, string message)
    {
        if (equipmentBusy || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        equipmentBusy = true;
        NotifyEquipment();
        try
        {
            var state = await equipmentApi.CommandAsync(connectedServer, connectedToken, action, payload, cancellation);
            cancellation.ThrowIfCancellationRequested();
            ApplyEquipment(state);
            OrderActionStatus = message;
            await RefreshAsync();
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception error) when (error is HttpRequestException or JsonException or IOException or OperationCanceledException)
        {
            OrderActionStatus = error is OperationCanceledException ? "장비 요청 시간 초과 · 회차 상태를 확인하세요." : error.Message;
            equipmentError = OrderActionStatus;
            if (error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally { equipmentBusy = false; NotifyEquipment(); }
    }

    public async Task StartEquipmentAsync()
    {
        if (!CanStartEquipment || SelectedOrder is null) return;
        if (startRequestId is null || requestJob != SelectedOrder.Id || requestPallet != DemoPalletId)
        {
            startRequestId = Guid.NewGuid();
            requestJob = SelectedOrder.Id;
            requestPallet = DemoPalletId;
        }
        await EquipmentActionAsync("start", new { job_id = SelectedOrder.Id, pallet_id = DemoPalletId,
            position_ready = PositionReady, request_id = startRequestId.ToString() }, "선택 주문으로 UNO 데모를 시작했습니다.");
        if (Equipment.Run?.JobId == requestJob && Equipment.Run.FinishedAt is null) startRequestId = null;
    }

    public Task StopEquipmentAsync() => EquipmentActionAsync("stop", new { }, "데모를 정지했습니다.");
    public Task ConfirmEquipmentAsync() => EquipmentActionAsync("confirm-ejection", new { }, Equipment.Mode == "REMOTE" ? "배출 확인 요청 · 라즈베리파이 응답을 기다립니다." : "배출 확인 · 장비 회차 성공을 기록했습니다.");
    public Task RetryEquipmentDatabaseAsync() => EquipmentActionAsync("database-retry", new { }, "장비 재운전 없이 DB 작업을 재시도했습니다.");

    public Task ApplyEquipmentTimingsAsync()
    {
        if (!int.TryParse(Ir1DelayMs, out int ir1) || !int.TryParse(RobotWaitMs, out int robot) || !int.TryParse(Ir2DelayMs, out int ir2))
        {
            OrderActionStatus = "세 시간은 ms 단위 정수로 입력하세요.";
            return Task.CompletedTask;
        }
        return EquipmentActionAsync("timings", new { values = new { ir1_delay_ms = ir1, robot_wait_ms = robot, ir2_delay_ms = ir2 } }, "UNO 데모 시간 설정을 적용했습니다.");
    }

    private void ResetEquipment()
    {
        Equipment = new();
        equipmentConnected = false;
        equipmentError = "";
        CameraFrame = null;
        Events.Clear();
        PositionReady = false;
        Notify(nameof(CameraFrame));
        NotifyEquipment();
    }
}
