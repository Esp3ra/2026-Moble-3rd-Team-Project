using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed record AgvDestination(int Point, string Name);

public sealed partial class DashboardViewModel
{
    private readonly AgvApiClient agvApi = new();
    private bool agvConnected, agvRefreshing, agvBusy;
    private string agvError = "";
    private Guid? agvRequest;
    private string? agvRequestJob;
    private int agvRequestDestination;
    private int agvDestinationPoint;
    public AgvState Agv { get; private set; } = new();
    public IReadOnlyList<AgvDestination> AgvDestinations { get; } = [new(3,"3 · 서울 HUB"),new(4,"4 · 평택 HUB"),new(5,"5 · 수원 HUB"),new(6,"6 · 오류1"),new(7,"7 · 오류2")];
    public int AgvDestinationPoint { get => agvDestinationPoint; set { agvDestinationPoint = value; Notify(); Notify(nameof(CanStartAgv)); } }
    public bool CanStartAgv => agvConnected && !agvBusy && Agv.CanStart && AgvDestinationPoint is >= 3 and <= 7 && SelectedOrder is not null && Agv.Eligible.Any(item => item.JobId == SelectedOrder.Id);
    public bool CanStopAgv => agvConnected && !agvBusy && Agv.CanStop;
    public string AgvSummary => !agvConnected ? "AGV DEMO · 연결 대기" : $"AGV DEMO · {Agv.Task?.State ?? "대기"} · 위치 {Agv.Device.Position}";
    public string AgvDetails => !agvConnected ? "AGV 서버 응답 대기" :
        $"위치 {Agv.Device.Position} · 지게 {Agv.Device.ServoAngle}° · {(Agv.Device.ForkMoving ? "지게 이동 중" : "지게 정지")}\n" +
        (Agv.Task is { } task ? $"{task.JobId} · {task.PalletId} · 목적지 {task.Destination}\n배송 {Math.Min(task.StepIndex + 1, 7)}/7 · {task.State}\n작업 {task.TaskId}" : "UNO 배출 확인 후 선택 주문을 배송합니다.");
    public string AgvRoute => "2 → 1 → 들기 110° → 2 → 목적지\n→ 하역 60° → 2 → 대기 85°";
    public string AgvAlert => !string.IsNullOrEmpty(agvError) ? agvError : Agv.Task?.Error ?? "";

    private void NotifyAgv()
    {
        foreach (var name in new[] { nameof(Agv),nameof(AgvSummary),nameof(AgvDetails),nameof(AgvAlert),nameof(CanStartAgv),nameof(CanStopAgv),nameof(Connections) }) Notify(name);
    }

    private async Task RefreshAgvAsync()
    {
        if (agvRefreshing || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        agvRefreshing = true;
        try
        {
            if (Agv.IsOwner && Agv.Task is { FinishedAt: null })
            {
                try { await agvApi.RequestAsync(connectedServer, connectedToken, "heartbeat", new { }, cancellation); }
                catch (HttpRequestException error) when (error.StatusCode == HttpStatusCode.Conflict) { }
            }
            var state = await agvApi.RequestAsync(connectedServer, connectedToken, "state", null, cancellation);
            cancellation.ThrowIfCancellationRequested();
            Agv = state;
            agvConnected = true;
            agvError = "";
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception error) when (error is HttpRequestException or JsonException or IOException or OperationCanceledException)
        {
            agvConnected = false;
            agvError = error is OperationCanceledException ? "AGV 응답 시간 초과" : error.Message;
            if (error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally { agvRefreshing = false; NotifyAgv(); }
    }

    private async Task AgvActionAsync(string action, object payload)
    {
        if (agvBusy || polling is null || connectedServer is null) return;
        var cancellation = polling.Token;
        agvBusy = true;
        NotifyAgv();
        try
        {
            var state = await agvApi.RequestAsync(connectedServer,connectedToken,action,payload,cancellation);
            cancellation.ThrowIfCancellationRequested();
            Agv = state;
            agvConnected = true;
            agvError = "";
            await RefreshAsync();
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception error) when (error is HttpRequestException or JsonException or IOException or OperationCanceledException)
        {
            agvError = error is OperationCanceledException ? "AGV 요청 시간 초과 · 배송 상태를 확인하세요." : error.Message;
            if (error is HttpRequestException { StatusCode: HttpStatusCode.Unauthorized }) SessionExpired?.Invoke();
        }
        finally { agvBusy = false; NotifyAgv(); }
    }
    public async Task StartAgvAsync()
    {
        if (!CanStartAgv || SelectedOrder is null) return;
        if (agvRequest is null || agvRequestJob != SelectedOrder.Id || agvRequestDestination != AgvDestinationPoint)
        {
            agvRequest = Guid.NewGuid(); agvRequestJob = SelectedOrder.Id; agvRequestDestination = AgvDestinationPoint;
        }
        await AgvActionAsync("start",new { job_id=SelectedOrder.Id,destination=AgvDestinationPoint,request_id=agvRequest.ToString() });
        if (Agv.Task?.JobId == agvRequestJob) agvRequest = null;
    }
    public Task StopAgvAsync() => AgvActionAsync("stop", new { });
    private void ResetAgv()
    {
        Agv = new(); agvConnected = false; agvError = ""; agvRequest = null;
        NotifyAgv();
    }
}
