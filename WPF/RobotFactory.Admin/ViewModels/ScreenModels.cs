using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Input;
namespace RobotFactory.Admin.ViewModels;

// 화면 전용 샘플 모델이다. 팀 공통 API/DTO/DB 계약을 정의하지 않는다.
public sealed record OrderPreview(string Id, string Model, string Parts, string State);
public sealed record PalletPreview(string Qr, string State, string Cycle, string Updated);
public sealed record EventPreview(string Time, string Message);
public sealed record PartPreview(string Part, string Expected, string Observed);

public sealed class DashboardViewModel
{
    public IReadOnlyList<string> Connections { get; } = ["업무 서버·DB  미연결", "Pi  미연결", "Arduino  미연결", "myCobot  미연결", "카메라  미연결", "AGV·ESP32  미연결"];
    public IReadOnlyList<string> ProcessSteps { get; } = ["1  주문·QR", "2  컨베이어 정차", "3  머리·팔 조립", "4  AGV 인계", "5  비전 검사", "6  목적지 이송"];
    public IReadOnlyList<OrderPreview> Orders { get; } = [
        new("ORD-024", "모델 A", "H1 · 왼팔 A2 · 오른팔 A2", "검사 중 · 예시"),
        new("ORD-023", "모델 B", "H2 · 왼팔 A1 · 오른팔 A1", "검사 보류 · 예시"),
        new("ORD-025", "모델 A", "H1 · 왼팔 A2 · 오른팔 A2", "대기 · 예시"),
        new("ORD-026", "모델 C", "H2 · 왼팔 A2 · 오른팔 A1", "대기 · 예시")];
    public IReadOnlyList<PalletPreview> Pallets { get; } = [
        new("PALLET-01", "INSPECT_1ST", "0", "10:24:08"),
        new("PALLET-02", "READY", "0", "10:17:07"),
        new("PALLET-03", "READY", "0", "10:17:07"),
        new("PALLET-04", "READY", "0", "10:23:10")];
    public IReadOnlyList<PartPreview> Parts { get; } = [new("머리", "H1", "수신 대기"), new("왼팔", "A2", "수신 대기"), new("오른팔", "A2", "수신 대기")];
    public IReadOnlyList<EventPreview> Events { get; } = [new("10:24:08", "ORD-024 · 검사대 도착 예시"), new("10:23:41", "ORD-024 · AGV 인계 예시"), new("10:22:56", "ORD-023 · 검사 보류 예시")];
    public string SelectedOrderId => "ORD-024";
    public string ExpectedQr => "PALLET-01";
    public string ScannedQr => "수신 없음";
    public string QrMatchState => "확인 대기";
}
public sealed class LoginViewModel { }
public sealed class MainViewModel : INotifyPropertyChanged
{
    private readonly DashboardViewModel dashboard = new();
    private readonly LoginViewModel login = new();
    private object currentView;
    public object CurrentView { get => currentView; private set { currentView = value; OnPropertyChanged(); } }
    public ICommand ShowDashboardCommand { get; }
    public ICommand ShowLoginCommand { get; }
    public MainViewModel()
    {
        currentView = dashboard;
        // 화면 미리보기 탐색이다. 로그인 성공이나 권한 부여를 의미하지 않는다.
        ShowDashboardCommand = new NavigationCommand(() => CurrentView = dashboard);
        ShowLoginCommand = new NavigationCommand(() => CurrentView = login);
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
