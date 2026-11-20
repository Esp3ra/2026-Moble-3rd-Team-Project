using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using System.Windows.Threading;
using RobotFactory.Admin.ViewModels;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        var dispatcher = Dispatcher.CurrentDispatcher;
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(dispatcher));
        dispatcher.BeginInvoke(new Action(async () =>
        {
            try { await VerifyAsync(); }
            catch (Exception error) { Console.Error.WriteLine(error); Environment.ExitCode = 1; }
            finally { dispatcher.BeginInvokeShutdown(DispatcherPriority.Normal); }
        }));
        Dispatcher.Run();
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private static async Task UntilAsync(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(7);
        while (!condition())
        {
            Check(DateTime.UtcNow < deadline, "Condition did not become true before timeout.");
            await Task.Delay(25);
        }
    }

    private static async Task<DashboardViewModel> LoginAsync(MainViewModel main, MockServer server)
    {
        var login = (LoginViewModel)main.CurrentView;
        login.ServerAddress = server.Address;
        login.LoginId = "synthetic-user";
        await login.LoginAsync("synthetic-password");
        Check(main.IsLoggedIn, "Mock login failed.");
        var dashboard = (DashboardViewModel)main.CurrentView;
        await dashboard.StartAsync();
        return dashboard;
    }

    private static void CheckCleared(MainViewModel main, DashboardViewModel dashboard)
    {
        Check(main.IsLoggedOut && main.CurrentView is LoginViewModel, "Login screen was not restored.");
        Check(dashboard.AdminToken == "" && dashboard.Orders.Count == 0 && dashboard.Pallets.Count == 0,
            "Session token or order/pallet data survived session expiry.");
        Check(dashboard.Equipment.Run is null && dashboard.Agv.Task is null &&
              dashboard.Events.Count == 0 && dashboard.CameraFrame is null && dashboard.SelectedOrder is null,
            "Equipment, AGV or selection data survived session expiry.");
        Check(!dashboard.CanDropOrder && !dashboard.CanStartEquipment && !dashboard.CanStartAgv,
            "Actions remained enabled after session expiry.");
    }

    private static async Task VerifyAsync()
    {
        using var server = new MockServer();
        using var main = new MainViewModel();
        var dashboard = await LoginAsync(main, server);
        Check(dashboard.Orders.Count == 1 && dashboard.Pallets.Count == 1, "Initial lists missing.");
        dashboard.PositionReady = true;
        Check(dashboard.CanStartEquipment, "Valid fresh data did not enable the UNO demo.");

        server.Company = "Updated order";
        server.PalletStatus = 503;
        await dashboard.RefreshAsync();
        Check(dashboard.Orders.Single().Company == server.Company, "Pallet 503 prevented order refresh.");
        Check(dashboard.Pallets.Count == 1 && dashboard.PalletsStatus.Contains("조회 실패") &&
              dashboard.OrdersStatus.Contains("수신"), "Independent list status was lost.");
        Check(!dashboard.CanDropOrder && !dashboard.CanStartEquipment && !dashboard.CanStartAgv,
            "Stale pallet data enabled an operation.");
        Console.WriteLine("PASS: orders 200 and pallets 503 update independently and block unsafe actions");

        server.PalletStatus = 200;
        server.OrderStatus = 503;
        await dashboard.RefreshAsync();
        Check(dashboard.PalletsStatus.Contains("수신") && dashboard.OrdersStatus.Contains("조회 실패"),
            "Order failure prevented pallet refresh.");
        Console.WriteLine("PASS: orders 503 and pallets 200 retain independent list status");

        server.PalletStatus = 401;
        await dashboard.RefreshAsync();
        CheckCleared(main, dashboard);
        Console.WriteLine("PASS: pallets 401 with orders 503 clears the session and all data");

        server.OrderStatus = server.PalletStatus = 200;
        dashboard = await LoginAsync(main, server);
        server.OrderStatus = 401;
        await dashboard.RefreshAsync();
        CheckCleared(main, dashboard);
        Console.WriteLine("PASS: orders 401 clears the session and all data");

        server.OrderStatus = 200;
        server.SessionSeconds = 3;
        dashboard = await LoginAsync(main, server);
        Check(dashboard.Equipment.Run is not null && dashboard.Agv.Task is not null, "Demo data was not loaded.");
        server.Dispose();
        await dashboard.RefreshAsync();
        Check(dashboard.OrdersStatus.Contains("조회 실패"), "Offline failure was not observed.");
        await UntilAsync(() => main.IsLoggedOut);
        CheckCleared(main, dashboard);
        Console.WriteLine("PASS: expires_at clears the session and all data while the server is offline");
    }
}

internal sealed class MockServer : IDisposable
{
    private readonly HttpListener listener = new();
    private bool disposed;
    public string Address { get; }
    public string Company = "Initial order";
    public int OrderStatus = 200;
    public int PalletStatus = 200;
    public int SessionSeconds = 60;

    public MockServer()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        int port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        Address = $"http://127.0.0.1:{port}";
        listener.Prefixes.Add(Address + "/");
        listener.Start();
        _ = ListenAsync();
    }

    private async Task ListenAsync()
    {
        try
        {
            while (listener.IsListening)
            {
                var context = await listener.GetContextAsync();
                _ = RespondAsync(context);
            }
        }
        catch (Exception error) when (error is HttpListenerException or ObjectDisposedException) { }
    }

    private async Task RespondAsync(HttpListenerContext context)
    {
        var response = context.Response;
        try
        {
            object body;
            switch (context.Request.Url!.AbsolutePath)
            {
                case "/api/auth/login":
                    body = new { token = "synthetic-token", expires_at = DateTimeOffset.UtcNow.ToUnixTimeSeconds() + SessionSeconds,
                        account = new { account_id = 1, login_id = "synthetic-user", name = "Synthetic", role_code = "ADMIN", role_name = "Admin" } };
                    break;
                case "/api/orders":
                    response.StatusCode = OrderStatus;
                    body = new { orders = new[] { new { id = "test-order", company = Company, factory_status = "PENDING", target_station = 3 } } };
                    break;
                case "/api/pallets":
                    response.StatusCode = PalletStatus;
                    body = new { pallets = new[] { new { pallet_id = "PALLET-01", current_status = "READY", cycle_count = 0 } } };
                    break;
                case "/api/equipment/state":
                    body = new { mode = "DEMO", state = "IDLE", can_start = true, trial = new { },
                        timings = new { values = new { } }, events = new object[] { new { name = "test-event" } },
                        run = new { job_id = "previous-order", finished_at = DateTimeOffset.UtcNow } };
                    break;
                case "/api/agv/state":
                    body = new { mode = "DEMO", device = new { }, can_start = true,
                        eligible = new[] { new { job_id = "test-order" } },
                        task = new { job_id = "previous-order", finished_at = "completed" } };
                    break;
                default:
                    response.StatusCode = 404;
                    body = new { error = "Mock endpoint unavailable" };
                    break;
            }
            response.ContentType = "application/json";
            await JsonSerializer.SerializeAsync(response.OutputStream, body);
        }
        catch (Exception error) when (error is HttpListenerException or ObjectDisposedException or IOException) { }
        finally { response.Close(); }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        listener.Close();
    }
}
