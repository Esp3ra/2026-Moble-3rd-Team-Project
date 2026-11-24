using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Input;

namespace RobotFactory.Admin.ViewModels;

// ── 통신규약 v0.2.0 고정 인자명 준수 (인자명 변경 불가) ─────────────────────
public static class RoleConstants
{
    public const string Admin = "ADMIN";             // 1차 관리자 (현장 라인 운영)
    public const string SuperAdmin = "SUPER_ADMIN";   // 최상위 관리자 (시스템 총괄)
}

public sealed record UserAccount(
    string account_id,
    string login_id,
    string display_name,
    string role
);

/// <summary>
/// 기능별 권한 매트릭스 (OpenAPI & 팀 규약 19, 20번 기준)
/// </summary>
public sealed class RolePermissions
{
    public string? Role { get; }
    public bool IsAuthenticated => !string.IsNullOrEmpty(Role) && (Role == RoleConstants.Admin || Role == RoleConstants.SuperAdmin);
    public bool IsSuperAdmin => IsAuthenticated && Role == RoleConstants.SuperAdmin;
    public bool IsAdminOrAbove => IsAuthenticated && (Role == RoleConstants.Admin || Role == RoleConstants.SuperAdmin);

    public RolePermissions(string? role)
    {
        // 유효한 역할만 허용하며, null/빈 문자열/알 수 없는 역할은 비인증(None)으로 격리
        Role = role is RoleConstants.Admin or RoleConstants.SuperAdmin ? role : null;
    }

    // ── [1차 관리자(ADMIN) & 최상위 관리자(SUPER_ADMIN) 공통 권한] ────────
    public bool CanApproveOrder => IsAdminOrAbove;       // 주문 수락 (생산 승인)
    public bool CanCancelOrder => IsAdminOrAbove;        // 대기 주문 취소
    public bool CanPauseResumePlant => IsAdminOrAbove;   // 설비 비상정지 / 재개
    public bool CanRetryQr => IsAdminOrAbove;            // 투입 QR 재판독 요청
    public bool CanReconcile => IsAdminOrAbove;          // 통신 복구 후 실물 상태 대조
    public bool CanManualAction => IsAdminOrAbove;       // 수동 장비 조작 (컨베이어/AGV)
    public bool CanViewCameras => IsAdminOrAbove;        // 실시간 카메라(CAM1~4) 조회
    public bool CanViewHistory => IsAdminOrAbove;        // 공정 이력/로그 열람
    public bool CanViewStock => IsAdminOrAbove;          // 현재 부품 재고 수량 확인

    // ── [최상위 관리자(SUPER_ADMIN) 전용 특수 권한] ───────────────────────
    public bool CanRestock => IsSuperAdmin;              // 부품 재입고 수량 등록 (POST /stock/restocks)
    public bool CanManageAccounts => IsSuperAdmin;       // 관리자 계정 생성/권한 변경 (POST /accounts)
    public bool CanQuerySettlement => IsSuperAdmin;      // 모의 결산 집계 조회 (POST /settlements/query)
    public bool CanChangeSettings => IsSuperAdmin;       // 설비 설정 프로필 변경 (GET/PUT /settings)
    public bool CanDeleteArchive => IsSuperAdmin;        // 보관자료 영구 삭제 (POST /archive/delete)

    public string RoleDisplayTitle => !IsAuthenticated
        ? "인증 대기 (로그인 필요)"
        : (IsSuperAdmin ? "최상위 관리자 (SUPER_ADMIN)" : "1차 관리자 (ADMIN)");
}

// ── 화면 전용 샘플 모델 ──────────────────────────────────────────────
public sealed record OrderPreview(string Id, string Model, string Parts, string State);
public sealed record PalletPreview(string Qr, string State, string Cycle, string Updated);
public sealed record EventPreview(string Time, string Message);
public sealed record PartPreview(string Part, string Expected, string Observed);

public sealed class DashboardViewModel : INotifyPropertyChanged
{
    private RolePermissions permissions = new(null); // 기본: 비인증 상태 (로그인 전에는 모든 버튼 비활성화)

    public RolePermissions Permissions
    {
        get => permissions;
        set { permissions = value; OnPropertyChanged(); }
    }

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

    // 권한 전환 테스트 및 세션 연동용 메서드
    public void SetRole(string? role)
    {
        Permissions = new RolePermissions(role);
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void OnPropertyChanged([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new(name));
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
