using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using RobotFactory.Admin.Services;
using RobotFactory.Admin.ViewModels;

static class Program
{
    [STAThread]
    static int Main()
    {
        var dispatcher=Dispatcher.CurrentDispatcher;
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext(dispatcher));
        var exit=0;
        dispatcher.BeginInvoke(async()=>
        {
            try {CheckRotation();await Run();await RunQr();await RunVideoMapping();await RunConveyor();Console.WriteLine("WPF regression: dashboard, QR ROI, video mapping and conveyor serial progress passed (mock HTTP, no hardware).");}
            catch(Exception error){Console.Error.WriteLine(error);exit=1;}
            finally{dispatcher.BeginInvokeShutdown(DispatcherPriority.Background);}
        });
        Dispatcher.Run();return exit;
    }
    static void Check(bool condition,string message){if(!condition)throw new Exception(message);}
    static void CheckRotation()
    {
        Check(HubNames.Label(3)=="3번 서울 HUB" && HubNames.Label(4)=="4번 대전 HUB" && HubNames.Label(5)=="5번 천안 HUB","updated hub display names");
        var source=new Rect(.2,.6,.4,.3);var display=VideoOrientation.ToDisplay(source);
        Check(Math.Abs(display.X-.1)<1e-9 && Math.Abs(display.Y-.2)<1e-9 && Math.Abs(display.Width-.3)<1e-9 && Math.Abs(display.Height-.4)<1e-9,"clockwise ROI rectangle");
        var a=VideoOrientation.ToSource(display.TopLeft);var b=VideoOrientation.ToSource(display.BottomRight);
        Check(Math.Abs(a.X-source.X)<1e-9 && Math.Abs(a.Y-source.Bottom)<1e-9 && Math.Abs(b.X-source.Right)<1e-9 && Math.Abs(b.Y-source.Y)<1e-9,"rotated selection maps back to original ROI");
        var bounds=VideoOrientation.ImageBounds(new Size(1000,500),1920,1080);
        Check(Math.Abs(bounds.Height-500)<1e-9 && Math.Abs(bounds.Width-281.25)<1e-9 && Math.Abs(bounds.X-359.375)<1e-9,"portrait rotated image letterbox geometry");
    }
    static async Task RunVideoMapping()
    {
        using var wire=new MockServer{VideoReady=true};using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        try
        {
            await view.StartAsync();await Until(()=>view.Cam1 is not null && view.Cam3 is not null);
            Check(((BitmapSource)view.Cam1!).PixelWidth==8 && ((BitmapSource)view.Cam3!).PixelWidth==3,"CAM1 full and CAM3 ROI have distinct sources");
            Check(wire.SeenFrames.Contains("qr-roi/frame") && !wire.SeenFrames.Contains("cameras/CAM3/frame"),"ROI remains available in transport test with physical CAM3 disabled");
        }
        finally{view.Stop();}
    }
    static async Task RunConveyor()
    {
        using var wire=new MockServer{Protocol="UNO_SIM_QR_V2"};using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        try
        {
            await view.StartAsync();Check(view.CanStart && view.StartLabel=="컨베이어 작업 시작","new firmware enables conveyor start");
            await view.PlantActionAsync("START");Check(wire.LastAction=="RUN_CYCLE","top start uses the bench full-cycle command");
            wire.WirelessBusy=true;
            foreach(var (stage,index) in new[]{("DISPENSING",0),("IR1_DELAY",1),("WAIT_QR",2),("ROBOT_WAIT",3),("IR2_DELAY",4),("WAIT_RETRACT",5)})
            {
                wire.Cycle=new("00000000-0000-4000-8000-000000000002",stage,"serial-event",DateTimeOffset.UtcNow,"PALLET-01",stage=="ROBOT_WAIT");
                await view.RefreshAsync();Check(view.ConveyorSteps.Count==6 && view.ConveyorSteps[index].Active,"serial progress highlights "+stage);
                Check(!view.CanStart && view.CanPause && !view.CanResume,"running cycle permits stop without duplicate start");
                if(stage=="WAIT_QR")Check(view.Summary.Contains("QR 성공 대기"),"QR gate shown as waiting");
                if(stage=="ROBOT_WAIT")Check(view.Summary.Contains("3초") && view.ConveyorDetail.Contains("UNO 확인"),"mock assembly begins only after QR acknowledgement");
            }
            wire.Cycle=wire.Cycle! with{Stage="DONE"};wire.WirelessBusy=false;await view.RefreshAsync();
            Check(view.ConveyorSteps.All(s=>s.Completed) && view.CanStart,"all six completed and ready for next cycle");
            wire.Cycle=wire.Cycle with{Stage="UNKNOWN"};await view.RefreshAsync();Check(!view.CanStart && view.CanPause,"unconfirmed result blocks restart");
            await view.PlantActionAsync("PAUSE");Check(wire.LastAction=="STOP","top stop sends bench STOP");
            wire.Cycle=wire.Cycle with{Stage="STOPPED"};await view.RefreshAsync();Check(view.CanStart,"acknowledged stop permits manual new start");
            wire.WirelessOnline=false;await view.RefreshAsync();Check(!view.CanStart && view.ConveyorSteps.All(s=>!s.Active && !s.Completed) && view.Summary.Contains("연결 끊김"),"disconnect clears live progress and prevents start");
        }
        finally{view.Stop();}
    }
    static async Task Until(Func<bool> condition)
    {
        for(var i=0;i<150;i++){if(condition())return;await Task.Delay(20);}
        throw new Exception("Timed out waiting for live QR state");
    }
    static async Task RunQr()
    {
        using var wire=new MockServer();using var http=new HttpClient(wire);var expired=false;
        using var view=new QrRoiViewModel(new Uri("http://127.0.0.1:8000"),"test-only",()=>expired=true,new FactoryApiClient(http));
        view.Start();await Until(()=>view.QrText=="PALLET-01");
        Check(view.CanSetRoi && view.Details.Contains("CLAHE"),"live ROI and correction metadata");
        await view.SetRoiAsync([.1,.2,.5,.4]);Check(wire.Revision==1 && view.Roi[0]==.1,"ROI saves revision and normalized coordinates");
        wire.QrState="NO_FRAME";await Until(()=>view.QrText=="—");Check(view.StateText=="카메라 영상 없음","disconnect clears decoded QR");
        wire.Fail="qr-roi/status";wire.Unauthorized=true;await Until(()=>expired);Check(!view.CanSetRoi,"401 disables ROI operations");
    }
    static async Task Run()
    {
        using var wire=new MockServer();using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        view.SessionExpired+=view.ClearSession;
        try
        {
            await view.StartAsync();Check(view.Orders.Single().Company=="old" && view.CanAccept,"initial snapshots");
            wire.Company="new";wire.Fail="pallets";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="new" && view.Pallets.Count==1,"pallet failure must preserve pallets and update orders");
            Check(!view.CanAccept && !view.CanStart,"stale required data must block process controls");
            await view.AcceptAsync();Check(wire.Posts==0,"direct action must also be blocked");
            Check(view.CanWirelessPing && view.CanPause,"independent wireless status and top STOP must remain usable");
            await view.WirelessActionAsync("PING");Check(wire.Posts==1,"wireless PING survives pallet failure");
            wire.Fail="orders";wire.Stage="TO_ASSEMBLY";wire.Units=17;await view.RefreshAsync();
            Check(view.Plant?.Stage=="TO_ASSEMBLY" && view.Stock.Single().AvailableUnits==17 && view.Orders.Single().Company=="new","order failure must not block plant or stock");
            wire.Fail="stock";wire.Company="stock-failure";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="stock-failure" && !view.CanAccept,"stock failure independent update");
            wire.Fail="plant";wire.Company="plant-failure";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="plant-failure" && !view.CanStart,"plant failure independent update");
            wire.Fail="";await view.RefreshAsync();Check(view.CanAccept,"recovery unlocks only after fresh snapshots");
            Check(view.CanWirelessMotion,"supported firmware enables one-second actuator controls");
            wire.Transport="LAN_HTTP_TEST";await view.RefreshAsync();Check(view.WirelessTitle.Contains("내부망"),"LAN mode label comes from server status");

            wire.Protocol="WIRELESS_BENCH";await view.RefreshAsync();
            Check(view.CanWirelessMotion && view.CanWirelessBelt && view.CanWirelessStop,"one-second bench firmware enables actuator, belt and stop");
            wire.Fail="wireless-test/status";wire.Company="wireless-failure";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="wireless-failure" && view.CanAccept && !view.CanWirelessPing,"wireless failure must not block standard lists; wireless commands blocked");
            wire.Fail="pallets";wire.Unauthorized=true;await view.RefreshAsync();
            Check(view.Orders.Count==0 && !view.CanAccept && !view.CanWirelessPing && view.AdminToken=="","401 must revoke the whole UI session");
        }
        finally{view.Stop();}
    }
}
sealed class MockServer:HttpMessageHandler
{
    public string Company="old",Fail="",Stage="READY",Protocol="WIRELESS_BENCH_10S",Transport="NGROK_HTTPS_TEST";
    public int Units=8,Posts,Revision;
    public string QrState="READ";
    public double[] Roi=[0,0,1,1];
    public bool Unauthorized;
    public bool VideoReady;
    public bool WirelessBusy,WirelessOnline=true;
    public ConveyorProgressV1? Cycle;
    public string? LastAction;
    public List<string> SeenFrames=[];
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken cancellation)
    {
        var path=request.RequestUri!.AbsolutePath.Replace("/api/v1/","");
        object data;var status=HttpStatusCode.OK;
        if(path==Fail){status=Unauthorized?HttpStatusCode.Unauthorized:HttpStatusCode.ServiceUnavailable;data=new{code="DEVICE_UNAVAILABLE",message="mock failure",retry="QUERY_FIRST",trace_id=Guid.NewGuid()};}
        else if(path is "qr-roi/status" or "qr-roi/roi")
        {
            if(request.Method==HttpMethod.Put){var input=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));Roi=input.RootElement.GetProperty("roi").EnumerateArray().Select(v=>v.GetDouble()).ToArray();Revision++;}
            data=new{state=QrState,pallet_id=QrState=="READ"?"PALLET-01":null,text=QrState=="READ"?"PALLET-01":"",decoded_values=new[]{"PALLET-01"},method="CLAHE",frame_id="mock-frame",captured_at=DateTimeOffset.UtcNow,revision=Revision,roi=Roi,resolution=new[]{1920,1080},requested_resolution=new[]{1920,1080},requested_fps=30,capture_fps=30,decode_fps=15};
        }
        else if(path.EndsWith("/frame"))
        {
            SeenFrames.Add(path);
            if(VideoReady)
            {
                var width=path=="qr-roi/frame"?3:8;var height=path=="qr-roi/frame"?4:6;
                var image=BitmapSource.Create(width,height,96,96,PixelFormats.Bgr24,null,new byte[width*height*3],width*3);
                var encoder=new JpegBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));using var stream=new System.IO.MemoryStream();encoder.Save(stream);
                var response=new HttpResponseMessage(HttpStatusCode.OK){Content=new ByteArrayContent(stream.ToArray())};response.Content.Headers.ContentType=new("image/jpeg");response.Headers.Add("X-Captured-At","2026-11-23T00:00:00Z");return response;
            }
            status=HttpStatusCode.ServiceUnavailable;data=new{};
        }
        else if(request.Method!=HttpMethod.Get){Posts++;if(path=="wireless-test/commands"){using var body=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));LastAction=body.RootElement.GetProperty("action").GetString();}data=new{};}
        else data=path switch
        {
            "orders"=>new{orders=new[]{new{order_id="00000000-0000-4000-8000-000000000001",spec=new{target_head_id="H1",target_arm_l_id="L1",target_arm_r_id="R1"},company=Company,phone="test",address="test",target_station=3,factory_status="PENDING",created_at=DateTimeOffset.UtcNow,updated_at=DateTimeOffset.UtcNow}},next_cursor=(string?)null},
            "plant"=>new{state="READY",stage=Stage,devices=Array.Empty<object>(),camera_ready=new{cam1=VideoReady,cam2=false,cam3=false,cam4=false},updated_at=DateTimeOffset.UtcNow},
            "pallets"=>new{pallets=new[]{new{pallet_id="PALLET-01",stage=Stage,updated_at=DateTimeOffset.UtcNow}},next_cursor=(string?)null},
            "stock"=>new{items=new[]{new{stock_key="ARM_1",available_units=Units}},next_cursor=(string?)null},
            "wireless-test/status"=>new{enabled=true,transport=Transport,online=WirelessOnline,serial_connected=true,serial_ready=true,protocol=Protocol,message="mock",busy=WirelessBusy,lines=Array.Empty<string>(),commands=Array.Empty<object>(),cycle=Cycle is null?null:new{command_id=Cycle.CommandId,stage=Cycle.Stage,line=Cycle.Line,changed_at=Cycle.ChangedAt,pallet_id=Cycle.PalletId,qr_confirmed=Cycle.QrConfirmed}},
            _=>new{events=Array.Empty<object>(),next_cursor=(string?)null}
        };
        var result=new HttpResponseMessage(status){Content=new StringContent(JsonSerializer.Serialize(data),Encoding.UTF8,"application/json")};
        if(VideoReady)result.Headers.Add("X-Mobot-Environment","TRANSPORT_TEST");return result;
    }
}
