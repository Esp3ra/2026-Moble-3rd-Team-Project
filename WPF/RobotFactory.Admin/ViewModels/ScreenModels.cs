using System.ComponentModel;
using System.IO;
using System.Windows.Input;
using System.Collections.ObjectModel;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Windows.Threading;
using RobotFactory.Admin.Services;
using System.Runtime.CompilerServices;
namespace RobotFactory.Admin.ViewModels;

// 주문·팔레트는 중앙 SQLite, UNO 영역은 인증된 데모 API를 조회한다.
public sealed record EventPreview(string Time, string Message);
public sealed record PartPreview(string Part, string Expected, string Observed);

public sealed partial class DashboardViewModel : INotifyPropertyChanged
{
    private readonly OrderApiClient api = new();
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromSeconds(1) };
    private CancellationTokenSource? polling;
    private bool refreshing;
    private bool dropping;
    private bool connectionReady;
    private int dataRevision;
    private string orderActionStatus = "검사 판정 수신 대기";
    private Uri? connectedServer;
    private string connectedToken = "";
    private FactoryOrder? selectedOrder;
    private string connectionStatus = "로그인 후 서버에 연결합니다.";
    public string ServerAddress { get; set; } = Environment.GetEnvironmentVariable("MOBOT_API_URL") ?? "http://127.0.0.1:8000";
    public string AdminToken { get; set; } = "";
    public event Action? SessionExpired;
    public ObservableCollection<FactoryOrder> Orders { get; } = [];
    public ObservableCollection<FactoryPallet> Pallets { get; } = [];
    public string OrderCount => $"최근 주문 · {Orders.Count}건";
    public string PalletCount => $"순환 팔레트 현황 · {Pallets.Count}기";
    public bool CanDropOrder => SelectedOrder?.FactoryStatus == "PENDING" && connectionReady && !dropping && !(Equipment.Run?.JobId == SelectedOrder.Id && Equipment.Run.FinishedAt is null);
    public string OrderActionStatus { get => orderActionStatus; private set { orderActionStatus = value; Notify(); } }
    public string ConnectionStatus { get => connectionStatus; private set { connectionStatus = value; Notify(); } }
    public FactoryOrder? SelectedOrder
    {
        get => selectedOrder;
        set
        {
            selectedOrder = value;
            AgvDestinationPoint = value?.TargetStation is >= 3 and <= 7 ? value.TargetStation.Value : 0;
            Notify();
            Notify(nameof(SelectedOrderId));
            Notify(nameof(Parts));
            Notify(nameof(CanDropOrder));
            NotifyEquipment();
            NotifyAgv();
        }
    }
    public DashboardViewModel()
    {
        timer.Tick += async (_, _) =>
        {
            await Task.WhenAll(RefreshEquipmentAsync(), RefreshAgvAsync());
            if (++equipmentTicks % 3 == 0) await RefreshAsync();
        };
        InitializeEquipmentPolling();
    }
    public async Task StartAsync()
    {
        Stop();
        if (!ApiConnection.TryParseAddress(ServerAddress, out var server))
        {
            ConnectionStatus = ApiConnection.AddressHint;
            return;
        }
        if (string.IsNullOrWhiteSpace(AdminToken))
        {
            ConnectionStatus = "계정으로 로그인해 주세요.";
            return;
        }
        if (AdminToken.Contains('\r') || AdminToken.Contains('\n'))
        {
            ConnectionStatus = "관리자 토큰에서 줄바꿈을 제거하세요.";
            return;
        }
        connectedServer = server;
        connectedToken = AdminToken.Trim();
        polling = new CancellationTokenSource();
        timer.Start();
        await RefreshAsync();
        await Task.WhenAll(RefreshEquipmentAsync(), RefreshAgvAsync());
        frameTimer.Start();
    }
    public void Stop()
    {
        connectionReady = false;
        Notify(nameof(CanDropOrder));
        timer.Stop();
        frameTimer.Stop();
        polling?.Cancel();
        polling?.Dispose();
        polling = null;
    }
    public async Task RefreshAsync()
    {
        if (refreshing || dropping || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        var revision = dataRevision;
        refreshing = true;
        Notify(nameof(CanDropOrder));
        try
        {
            var orderRequest = api.GetOrdersAsync(connectedServer, connectedToken, cancellation);
            var palletRequest = api.GetPalletsAsync(connectedServer, connectedToken, cancellation);
            var requests = Task.WhenAll(orderRequest, palletRequest);
            try
            {
                await requests;
            }
            catch when (requests.Exception?.InnerExceptions.Any(error =>
                error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) == true)
            {
                cancellation.ThrowIfCancellationRequested();
                SessionExpired?.Invoke();
                return;
            }
            var orders = (await orderRequest).Where(order => order.FactoryStatus != "CANCELED").ToArray();
            var pallets = await palletRequest;
            cancellation.ThrowIfCancellationRequested();
            // A response started before a drop must not restore the canceled order.
            if (revision != dataRevision) return;
            var selectedId = SelectedOrder?.Id;
            // Keep the selected item stable when an unchanged response arrives.
            if (!Orders.SequenceEqual(orders))
            {
                Orders.Clear();
                foreach (var order in orders) Orders.Add(order);
                SelectedOrder = Orders.FirstOrDefault(order => order.Id == selectedId) ?? Orders.FirstOrDefault();
                Notify(nameof(OrderCount));
            }
            if (!Pallets.SequenceEqual(pallets))
            {
                var chosenPallet = DemoPalletId;
                Pallets.Clear();
                foreach (var pallet in pallets) Pallets.Add(pallet);
                Notify(nameof(PalletCount));
                DemoPalletId = Pallets.Any(pallet => pallet.PalletId == chosenPallet) ? chosenPallet :
                    Pallets.FirstOrDefault(pallet => pallet.CurrentStatus == "READY")?.PalletId ?? "";
            }
            NotifyEquipment();
            ConnectionStatus = $"서버·DB 연결됨 · {DateTime.Now:HH:mm:ss} 갱신 · 3초 간격 · 주문 최신 100건 · 팔레트 {Pallets.Count}기";
            connectionReady = true;
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException or IOException)
        {
            connectionReady = false;
            ConnectionStatus = exception is OperationCanceledException
                ? "응답 시간 초과 · 마지막 수신 정보 표시 중 · 자동 재시도"
                : $"조회 실패 · 마지막 수신 정보 표시 중 · {exception.Message}";
        }
        finally { refreshing = false; Notify(nameof(CanDropOrder)); }
    }

    public async Task DropOrderAsync(FactoryOrder order)
    {
        if (dropping || !connectionReady || order.FactoryStatus != "PENDING" || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        dropping = true;
        dataRevision++;
        Notify(nameof(CanDropOrder));
        OrderActionStatus = "주문 드롭 요청 중";
        try
        {
            var canceled = await api.CancelOrderAsync(connectedServer, connectedToken, order.Id, cancellation);
            cancellation.ThrowIfCancellationRequested();
            if (canceled.FactoryStatus != "CANCELED")
                throw new JsonException("주문 취소 상태를 확인할 수 없습니다.");
            var displayed = Orders.FirstOrDefault(item => item.Id == order.Id);
            if (displayed is not null) Orders.Remove(displayed);
            if (SelectedOrder is null || SelectedOrder.Id == order.Id) SelectedOrder = Orders.FirstOrDefault();
            Notify(nameof(OrderCount));
            OrderActionStatus = "선택 주문을 드롭했습니다.";
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
        {
            OrderActionStatus = "연결 변경으로 요청이 중단됐습니다. 주문 상태를 다시 확인하세요.";
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException or IOException)
        {
            OrderActionStatus = exception is OperationCanceledException
                ? "응답 시간 초과 · 주문 상태를 다시 확인하거나 재시도하세요."
                : exception.Message;
            if (exception is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally { dropping = false; Notify(nameof(CanDropOrder)); }
    }
    public void ClearSession()
    {
        Stop();
        dataRevision++;
        AdminToken = "";
        connectedToken = "";
        connectedServer = null;
        Orders.Clear();
        Pallets.Clear();
        SelectedOrder = null;
        Notify(nameof(OrderCount));
        Notify(nameof(PalletCount));
        ResetEquipment();
        ResetAgv();
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
    public IReadOnlyList<string> Connections => ["주문 서버·DB  연결 상태↓", "Pi  미연결", equipmentConnected ? "UNO  DEMO 연결" : "UNO  조회 대기", "myCobot  미연결", equipmentConnected ? "카메라  DEMO 영상" : "카메라  영상 대기", agvConnected ? "AGV  DEMO 연결" : "AGV  조회 대기"];
    public IReadOnlyList<string> ProcessSteps { get; } = ["1  주문 선택", "2  배출·QR", "3  IR1 정차", "4  작업 가정 대기", "5  IR2 정차", "6  배출 확인"];
    public IReadOnlyList<PartPreview> Parts => [new("머리", SelectedOrder?.TargetHeadId ?? "—", "수신 대기"), new("왼팔", SelectedOrder?.TargetArmLId ?? "—", "수신 대기"), new("오른팔", SelectedOrder?.TargetArmRId ?? "—", "수신 대기")];
    public string SelectedOrderId => SelectedOrder?.Id ?? "주문 선택 대기";
    private bool SelectedRun => SelectedOrder is not null && Equipment.Run?.JobId == SelectedOrder.Id;
    public string ExpectedQr => SelectedRun ? Equipment.Run!.ExpectedPalletId : "배정 대기";
    public string ScannedQr => SelectedRun ? Equipment.Run?.PalletId ?? Equipment.Trial.PalletId ?? "인식 대기" : "인식 대기";
    public string QrMatchState => SelectedRun && Equipment.Run?.MappingId is not null ? "일치 · DB 연결" : "확인 대기";

}
public sealed class LoginViewModel : INotifyPropertyChanged
{
    private readonly AuthApiClient api = new();
    private bool busy;
    private string status = "아이디와 비밀번호로 로그인하세요.";
    public string ServerAddress { get; set; } = Environment.GetEnvironmentVariable("MOBOT_API_URL") ?? "http://127.0.0.1:8000";
    public string LoginId { get; set; } = "";
    public bool CanSubmit => !busy;
    public string Status { get => status; private set { status = value; Notify(); } }
    public event Action<Uri, AdminSession>? LoggedIn;
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
    public void SetStatus(string message) => Status = message;

    public async Task LoginAsync(string password)
    {
        if (busy) return;
        if (!ApiConnection.TryParseAddress(ServerAddress, out var server))
        {
            Status = ApiConnection.AddressHint;
            return;
        }
        if (string.IsNullOrWhiteSpace(LoginId) || string.IsNullOrEmpty(password))
        {
            Status = "아이디와 비밀번호를 입력하세요.";
            return;
        }
        busy = true;
        Notify(nameof(CanSubmit));
        Status = "로그인 확인 중";
        try
        {
            var session = await api.LoginAsync(server, LoginId.Trim(), password);
            Status = "로그인했습니다.";
            LoggedIn?.Invoke(server, session);
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or JsonException or IOException)
        {
            Status = exception is OperationCanceledException ? "서버 응답 시간이 초과됐습니다." : exception.Message;
        }
        finally { busy = false; Notify(nameof(CanSubmit)); }
    }
}

public sealed class MainViewModel : INotifyPropertyChanged
{
    private DashboardViewModel? dashboard;
    private readonly LoginViewModel login = new();
    private readonly AuthApiClient auth = new();
    private AdminSession? session;
    private Uri? server;
    private object currentView;
    public object CurrentView { get => currentView; private set { currentView = value; OnPropertyChanged(); } }
    public bool IsLoggedIn => session is not null;
    public bool IsLoggedOut => !IsLoggedIn;
    public string SessionDisplay => session is null ? "관리자 로그인이 필요합니다." : $"{session.Account.Name} · {session.Account.DisplayRole} · {session.Account.LoginId}";
    public ICommand ShowDashboardCommand { get; }
    public ICommand ShowLoginCommand { get; }
    public MainViewModel()
    {
        currentView = login;
        login.LoggedIn += StartSession;
        ShowDashboardCommand = new NavigationCommand(() => { if (IsLoggedIn && dashboard is not null) CurrentView = dashboard; });
        ShowLoginCommand = new NavigationCommand(() => { if (!IsLoggedIn) CurrentView = login; });
    }
    private void StartSession(Uri address, AdminSession authenticated)
    {
        session = authenticated;
        server = address;
        dashboard = new DashboardViewModel { ServerAddress = address.ToString(), AdminToken = authenticated.Token };
        dashboard.SessionExpired += () => EndSession("세션이 만료되었습니다. 다시 로그인하세요.");
        CurrentView = dashboard;
        NotifySession();
    }
    private void EndSession(string message)
    {
        dashboard?.ClearSession();
        dashboard = null;
        session = null;
        server = null;
        login.SetStatus(message);
        CurrentView = login;
        NotifySession();
    }
    public async Task LogoutAsync()
    {
        if (session is null || server is null) return;
        var token = session.Token;
        var address = server;
        EndSession("로그아웃했습니다.");
        try { await auth.LogoutAsync(address, token); }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException or IOException)
        {
            if (!IsLoggedIn) login.SetStatus("앱에서 로그아웃했습니다. 서버 세션 해제 응답을 받지 못했습니다.");
        }
    }
    private void NotifySession()
    {
        OnPropertyChanged(nameof(IsLoggedIn));
        OnPropertyChanged(nameof(IsLoggedOut));
        OnPropertyChanged(nameof(SessionDisplay));
    }
    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
}
internal sealed class NavigationCommand(Action action) : ICommand
{
    public bool CanExecute(object? parameter) => true;
    public void Execute(object? parameter) => action();
    public event EventHandler? CanExecuteChanged { add { } remove { } }
}
