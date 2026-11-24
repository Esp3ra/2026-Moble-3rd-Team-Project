using System.ComponentModel;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows.Input;
using System.Runtime.CompilerServices;
using RobotFactory.Admin.Services;
namespace RobotFactory.Admin.ViewModels;

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
        if (!Uri.TryCreate(ServerAddress, UriKind.Absolute, out var server) ||
            (server.Scheme != "http" && server.Scheme != "https") || !string.IsNullOrEmpty(server.UserInfo))
        {
            Status = "http 또는 https 서버 주소를 입력하세요.";
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
    public string SessionDisplay => session is null ? "관리자 로그인이 필요합니다." : $"{session.Account.DisplayName} · {session.Account.DisplayRole}";
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
        dashboard = new DashboardViewModel { ServerAddress = address.ToString(), AdminToken = authenticated.Token, IsSuperAdmin = authenticated.Account.Role == "SUPER_ADMIN" };
        dashboard.SessionExpired += () => EndSession("로그인이 해제되었거나 권한이 변경되었습니다. 다시 로그인하세요.");
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
