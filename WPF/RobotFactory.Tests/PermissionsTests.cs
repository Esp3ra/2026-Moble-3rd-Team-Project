using RobotFactory.Admin.ViewModels;
using Xunit;

namespace RobotFactory.Tests;

public class PermissionsTests
{
    [Fact]
    public void Unauthenticated_WhenRoleIsNull_HasNoPermissions()
    {
        var permissions = new RolePermissions(null);

        Assert.Null(permissions.Role);
        Assert.False(permissions.IsAuthenticated);
        Assert.False(permissions.IsSuperAdmin);
        Assert.False(permissions.IsAdminOrAbove);

        // 공통 조작 권한 차단 검증
        Assert.False(permissions.CanApproveOrder);
        Assert.False(permissions.CanCancelOrder);
        Assert.False(permissions.CanPauseResumePlant);
        Assert.False(permissions.CanRetryQr);
        Assert.False(permissions.CanReconcile);
        Assert.False(permissions.CanManualAction);
        Assert.False(permissions.CanViewCameras);
        Assert.False(permissions.CanViewHistory);
        Assert.False(permissions.CanViewStock);

        // 최상위 권한 차단 검증
        Assert.False(permissions.CanRestock);
        Assert.False(permissions.CanManageAccounts);
        Assert.False(permissions.CanQuerySettlement);
        Assert.False(permissions.CanChangeSettings);
        Assert.False(permissions.CanDeleteArchive);

        Assert.Equal("인증 대기 (로그인 필요)", permissions.RoleDisplayTitle);
    }

    [Theory]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData("UNKNOWN_ROLE")]
    [InlineData("GUEST")]
    [InlineData("OPERATOR")]
    public void Unauthenticated_WhenRoleIsInvalidOrUnknown_IsolatedToUnauthenticated(string? invalidRole)
    {
        var permissions = new RolePermissions(invalidRole);

        Assert.Null(permissions.Role);
        Assert.False(permissions.IsAuthenticated);
        Assert.False(permissions.IsSuperAdmin);
        Assert.False(permissions.IsAdminOrAbove);
        Assert.False(permissions.CanApproveOrder);
        Assert.False(permissions.CanPauseResumePlant);
        Assert.False(permissions.CanRestock);
        Assert.Equal("인증 대기 (로그인 필요)", permissions.RoleDisplayTitle);
    }

    [Fact]
    public void AdminRole_HasAdminPermissions_ButNotSuperAdminPermissions()
    {
        var permissions = new RolePermissions(RoleConstants.Admin);

        Assert.Equal(RoleConstants.Admin, permissions.Role);
        Assert.True(permissions.IsAuthenticated);
        Assert.False(permissions.IsSuperAdmin);
        Assert.True(permissions.IsAdminOrAbove);

        // 1차 관리자 허용 권한
        Assert.True(permissions.CanApproveOrder);
        Assert.True(permissions.CanCancelOrder);
        Assert.True(permissions.CanPauseResumePlant);
        Assert.True(permissions.CanRetryQr);
        Assert.True(permissions.CanReconcile);
        Assert.True(permissions.CanManualAction);
        Assert.True(permissions.CanViewCameras);
        Assert.True(permissions.CanViewHistory);
        Assert.True(permissions.CanViewStock);

        // 최상위 관리자 전용 권한은 차단되어야 함
        Assert.False(permissions.CanRestock);
        Assert.False(permissions.CanManageAccounts);
        Assert.False(permissions.CanQuerySettlement);
        Assert.False(permissions.CanChangeSettings);
        Assert.False(permissions.CanDeleteArchive);

        Assert.Equal("1차 관리자 (ADMIN)", permissions.RoleDisplayTitle);
    }

    [Fact]
    public void SuperAdminRole_HasAllPermissions()
    {
        var permissions = new RolePermissions(RoleConstants.SuperAdmin);

        Assert.Equal(RoleConstants.SuperAdmin, permissions.Role);
        Assert.True(permissions.IsAuthenticated);
        Assert.True(permissions.IsSuperAdmin);
        Assert.True(permissions.IsAdminOrAbove);

        // 모든 조작 권한 허용
        Assert.True(permissions.CanApproveOrder);
        Assert.True(permissions.CanCancelOrder);
        Assert.True(permissions.CanPauseResumePlant);
        Assert.True(permissions.CanRetryQr);
        Assert.True(permissions.CanReconcile);
        Assert.True(permissions.CanManualAction);
        Assert.True(permissions.CanViewCameras);
        Assert.True(permissions.CanViewHistory);
        Assert.True(permissions.CanViewStock);

        // 최상위 관리자 특수 권한 허용
        Assert.True(permissions.CanRestock);
        Assert.True(permissions.CanManageAccounts);
        Assert.True(permissions.CanQuerySettlement);
        Assert.True(permissions.CanChangeSettings);
        Assert.True(permissions.CanDeleteArchive);

        Assert.Equal("최상위 관리자 (SUPER_ADMIN)", permissions.RoleDisplayTitle);
    }

    [Fact]
    public void DashboardViewModel_InitialState_IsUnauthenticated()
    {
        var vm = new DashboardViewModel();

        // 프로그램 켜졌을 때 즉각적인 버튼/배지 활성화 차단 검증
        Assert.False(vm.Permissions.IsAuthenticated);
        Assert.False(vm.Permissions.CanApproveOrder);
        Assert.False(vm.Permissions.CanPauseResumePlant);
        Assert.Equal("인증 대기 (로그인 필요)", vm.Permissions.RoleDisplayTitle);
    }

    [Fact]
    public void DashboardViewModel_SetRole_UpdatesPermissionsAndSupportsLogout()
    {
        var vm = new DashboardViewModel();
        bool propertyChangedRaised = false;
        vm.PropertyChanged += (sender, args) =>
        {
            if (args.PropertyName == nameof(DashboardViewModel.Permissions))
            {
                propertyChangedRaised = true;
            }
        };

        // 1. 로그인 성공 (ADMIN)
        vm.SetRole(RoleConstants.Admin);
        Assert.True(propertyChangedRaised);
        Assert.True(vm.Permissions.IsAuthenticated);
        Assert.True(vm.Permissions.CanApproveOrder);
        Assert.False(vm.Permissions.CanRestock);

        // 2. 권한 변경 (SUPER_ADMIN)
        propertyChangedRaised = false;
        vm.SetRole(RoleConstants.SuperAdmin);
        Assert.True(propertyChangedRaised);
        Assert.True(vm.Permissions.CanRestock);

        // 3. 로그아웃 또는 세션 만료 (null)
        propertyChangedRaised = false;
        vm.SetRole(null);
        Assert.True(propertyChangedRaised);
        Assert.False(vm.Permissions.IsAuthenticated);
        Assert.False(vm.Permissions.CanApproveOrder);
        Assert.False(vm.Permissions.CanPauseResumePlant);
        Assert.Equal("인증 대기 (로그인 필요)", vm.Permissions.RoleDisplayTitle);
    }
}
