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
            try {CheckRotation();await Run();await RunQr();await RunReadOnlyQr();await RunStandardCameraQr();await RunVideoMapping();await RunPlant();await RunQrTickets();await RunMixedTrial();Console.WriteLine("WPF regression: dashboard, QR ROI, video mapping, plant and mixed trial passed (mock HTTP, no hardware).");}
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
    static async Task RunMixedTrial()
    {
        using var wire=new MockServer();using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        try
        {
            await view.StartAsync();
            Check(!view.IsMixedTrial && !view.CanRunMixedTrial,"production never exposes trial controls");
            await view.StartMixedTrialAsync();Check(wire.Posts==0,"trial action is blocked in production");
            wire.MixedTrial=true;await view.RefreshAsync();
            Check(view.IsMixedTrial && view.EnvironmentLabel.Contains("YOLO 모의"),"mixed trial is explicitly labelled");
            var beforeRecovery=wire.Posts;
            await view.RecoverStandbyAsync();Check(wire.Posts==beforeRecovery,"operator physical standby confirmation is required");
            wire.DropRecoveryReply=true;view.StandbyConfirmed=true;await view.RecoverStandbyAsync();
            await view.RecoverStandbyAsync();
            Check(wire.RecoveryRequests.Count==2 && wire.RecoveryRequests[0]==wire.RecoveryRequests[1],"recovery retries keep the original UUID after response loss");
            Check(!view.StandbyConfirmed && view.ActionStatus.Contains("복구 완료"),"manual completion is queried and physical confirmation is cleared");
            wire.TrialPaths.Clear();
            wire.DropTrialReply=true;
            view.SelectedTrialDestination=view.TrialDestinations[0];await view.StartMixedTrialAsync();
            var first=wire.TrialRequests.Single();
            Check(!view.CanSelectTrialDestination && view.CanRunMixedTrial,"lost submission reply retains the same pending trial");
            view.SelectedTrialDestination=view.TrialDestinations[2];await view.StartMixedTrialAsync();
            Check(wire.TrialRequests[1]==first && wire.TrialStations.SequenceEqual(new[]{3,3}),"retry preserves UUID and captured destination");
            foreach(var target in view.TrialDestinations.Skip(1))
            {view.SelectedTrialDestination=target;await view.StartMixedTrialAsync();}
            Check(wire.TrialStations.SequenceEqual(new[]{3,3,4,5}),"three destination selections map to existing order address rules");
            Check(wire.LastAction=="START" && wire.TrialPaths.All(p=>p=="orders" || p.EndsWith("/accept") || p=="plant/actions"),"trial uses existing order/approval/plant endpoints only");
            wire.State="RUNNING";wire.CycleId=Guid.NewGuid().ToString();await view.RefreshAsync();
            Check(!view.CanRunMixedTrial,"active cycle blocks another trial");
            Check(!view.CanRecoverStandby,"active cycle blocks idle reset");
        }
        finally{view.Stop();}
    }
    static async Task RunVideoMapping()
    {
        using var wire=new MockServer{VideoReady=true};using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        try
        {
            await view.StartAsync();await Until(()=>view.Cam1 is not null && view.Cam2 is not null && view.Cam3 is not null && view.Cam4 is not null);
            Check(((BitmapSource)view.Cam1!).PixelWidth==8 && ((BitmapSource)view.Cam3!).PixelWidth==8,"CAM1 and CAM3 both show their full assigned camera");
            Check(Enumerable.Range(1,4).All(index=>wire.SeenFrames.Contains($"cameras/CAM{index}/frame")) &&
                !wire.SeenFrames.Contains("qr-roi/frame"),"camera snapshots use their four assigned endpoints; ROI has a dedicated reader");
        }
        finally{view.Stop();}
    }
    static async Task RunPlant()
    {
        using var wire=new MockServer{HistoricalOrderCycle="00000000-0000-4000-8000-000000000009"};using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        try
        {
            await view.StartAsync();
            Check(view.SelectedOrder?.PalletId=="PALLET-08" && view.QrSummary.Contains("회차 대기"),"the selected historical order does not supply the live feed QR");
            Check(view.CanStart,"WPF starts through normal plant readiness");
            Check(view.HeaderDevices.Single().DeviceId=="LINE","LINE readiness comes from the plant snapshot");
            await view.PlantActionAsync("START");
            Check(wire.LastAction=="START" && wire.LastPostPath=="plant/actions","top start uses the standard plant API");
            wire.State="RUNNING";
            wire.CycleId="00000000-0000-4000-8000-000000000010";
            foreach(var (stage,index) in new[]{("DISPENSING",0),("TO_ASSEMBLY",1),("WAIT_FEED_QR",1),
                ("ASSEMBLING",2),("INSPECTING",2),("TO_PICKUP",2)})
            {
                wire.Stage=stage;
                await view.RefreshAsync();
                Check(view.ProcessSteps.Count==5 && view.ProcessSteps[index].Active,"plant progress highlights "+stage);
                Check(view.ProcessSteps.Take(index).All(step=>step.Completed),"previous stages completed before "+stage);
                Check(!view.ProcessSteps[^1].Completed,"conveyor waits for IR2 stop evidence");
                Check(!view.CanStart && view.CanPause && !view.CanResume,"running cycle permits pause and blocks duplicate start");
                if(stage=="WAIT_FEED_QR")
                {
                    wire.FeedState="UNREADABLE";await view.RefreshAsync();
                    Check(view.Summary.Contains("재판독") && view.CanReadQr && !view.ProcessSteps[2].Active,"QR reader failure holds the QR step");
                    Check(view.QrSummary.Contains("판독 실패") && !view.QrSummary.Contains("PALLET-08"),"the current read failure never displays another cycle's pallet");
                    var posts=wire.Posts;
                    await view.RetryQrAsync("SAVE_BINDING");
                    wire.State="PAUSED";await view.RefreshAsync();
                    await view.RetryQrAsync("READ_QR");
                    Check(!view.CanReadQr && wire.Posts==posts,"paused or incompatible QR retries must not be submitted");
                    wire.State="RUNNING";
                    wire.FeedState="SAVE_FAILED";await view.RefreshAsync();
                    Check(view.Summary.Contains("저장") && view.CanSaveQr && !view.ProcessSteps[1].Completed,"DB save failure holds the QR step");
                    wire.FeedState=null;
                }
                if(stage=="TO_ASSEMBLY")
                {
                    wire.FeedState="BOUND";wire.PalletCycle=wire.HistoricalOrderCycle;await view.RefreshAsync();
                    Check(view.QrSummary.Contains("번호 조회 대기") && !view.QrSummary.Contains("PALLET-"),"a stale pallet snapshot cannot be matched to the new cycle");
                    wire.PalletCycle=wire.CycleId;await view.RefreshAsync();
                    Check(view.QrSummary.Contains("PALLET-01") && !view.QrSummary.Contains("PALLET-08"),"the live QR uses the pallet assigned to the active cycle");
                }
                if(stage=="ASSEMBLING")
                {
                    Check(view.Summary.Contains("대기 자세"),"hardware waits for actual arm standby");
                    wire.EnvironmentName="SIMULATION";await view.RefreshAsync();
                    Check(view.ProcessSteps[2].Label=="조립/검사" && view.Summary.Contains("모의"),"simulation labels its arm work in detailed status");
                    wire.EnvironmentName="HARDWARE";
                }
            }
            wire.Stage="PICKING_UP";await view.RefreshAsync();
            Check(view.ProcessSteps[2].Active && !view.ProcessSteps[3].Active,"AGV pickup waits for command acceptance");
            wire.AgvAccepted=true;await view.RefreshAsync();
            Check(view.ProcessSteps[3].Active,"AGV receipt activates the pickup step");
            wire.Stage="UNLOADING";await view.RefreshAsync();
            Check(view.ProcessSteps[3].Completed && !view.ProcessSteps[4].Completed,"unload waits for completion evidence");
            wire.Stage="RETURNING";await view.RefreshAsync();
            Check(view.ProcessSteps.All(step=>step.Completed && !step.Active),"return stage confirms unload completion");
            await view.PlantActionAsync("PAUSE");
            Check(wire.LastAction=="PAUSE" && wire.LastPostPath=="plant/actions","top stop uses the standard plant API");
            wire.State="PAUSED";await view.RefreshAsync();
            await view.PlantActionAsync("RESUME");
            Check(wire.LastAction=="RESUME" && wire.LastPostPath=="plant/actions","top resume uses the standard plant API");
            wire.State="READY";wire.Stage="READY";wire.CycleId=null;
            await view.RefreshAsync();
            Check(view.Summary.Contains("다음 작업 대기") && view.ProcessSteps.All(step=>step.Completed),"last confirmed conveyor completion remains visible at ready");
            wire.State="RUNNING";wire.Stage="DISPENSING";wire.CycleId="00000000-0000-4000-8000-000000000011";
            await view.RefreshAsync();
            Check(view.ProcessSteps[0].Active && view.ProcessSteps.All(step=>!step.Completed),"the next cycle clears completion");
            wire.Fail="plant";await view.RefreshAsync();
            Check(!view.CanStart && !view.CanPause && view.ProcessSteps.All(step=>!step.Active && !step.Completed),"failed plant snapshot clears live progress and blocks actions");
            Check(view.QrSummary.Contains("공정 상태 확인 대기"),"failed plant snapshots clear the live QR summary");
            Check(!wire.RequestedBench,"the WPF must never request the legacy bench endpoints");
        }
        finally{view.Stop();}
    }
    static async Task Until(Func<bool> condition)
    {
        for(var i=0;i<150;i++){if(condition())return;await Task.Delay(20);}
        throw new Exception("Timed out waiting for live QR state");
    }
    static async Task RunQrTickets()
    {
        using var wire=new MockServer{CycleId=Guid.NewGuid().ToString(),State="RUNNING",Stage="WAIT_FEED_QR",FeedState="UNREADABLE"};
        using var http=new HttpClient(wire);
        var view=new DashboardViewModel(new FactoryApiClient(http)){AdminToken="test-only"};
        view.SessionExpired+=view.ClearSession;
        try
        {
            await view.StartAsync();
            wire.DropQrPostReply=true;wire.QrGetStatus=HttpStatusCode.ServiceUnavailable;
            await view.RetryQrAsync("READ_QR");
            Check(wire.QrPosts.Count==1 && wire.QrGets>0 && !view.CanReadQr && view.ActionStatus.Contains("미확인"),"a lost POST reply polls the original ticket without repeating the POST");
            wire.Company="ticket-outage";await view.RefreshAsync();await view.RetryQrAsync("READ_QR");
            Check(view.Orders.Single().Company=="ticket-outage" && wire.QrPosts.Count==1,"ticket failure neither blocks snapshots nor resends the command");
            wire.DropQrPostReply=false;wire.QrGetStatus=HttpStatusCode.OK;wire.QrTicketState="RUNNING";
            await view.RefreshAsync();Check(view.ActionStatus.Contains("진행 중"),"the lookup recovers the read ID for the accepted request");
            wire.Fail="pallets";wire.QrTicketState="COMPLETED";
            foreach(var field in new[]{"request","cycle","read"})
            {
                wire.QrMismatch=field;await view.RefreshAsync();
                Check(view.ActionStatus.Contains("미확인") && !view.ActionStatus.Contains("QR 재시도 완료"),"tickets with unrelated "+field+" cannot finish the retry");
            }
            wire.QrMismatch="";wire.QrTicketState="UNKNOWN";await view.RefreshAsync();
            Check(view.ActionStatus.Contains("미확인") && !view.CanReadQr,"UNKNOWN remains queued for lookup");
            wire.QrTicketState="COMPLETED";await view.RefreshAsync();
            Check(view.ActionStatus.Contains("QR 재시도 완료") && wire.QrPosts.Count==1,"a confirmed ticket completes even with the pallet endpoint offline");
            var gets=wire.QrGets;await view.RefreshAsync();Check(wire.QrGets==gets,"completed tickets stop polling");

            wire.Fail="";wire.FeedState="SAVE_FAILED";await view.RefreshAsync();
            wire.DropQrPostReply=true;wire.QrGetStatus=HttpStatusCode.NotFound;
            await view.RetryQrAsync("SAVE_BINDING");
            Check(view.CanSaveQr && view.ActionStatus.Contains("접수 기록 없음"),"a missing record allows the operator to retry explicitly");
            wire.DropQrPostReply=false;wire.QrGetStatus=HttpStatusCode.OK;wire.QrTicketState="FAILED";
            await view.RetryQrAsync("SAVE_BINDING");
            Check(wire.QrPosts.Count==3 && wire.QrPosts[1]==wire.QrPosts[2] && view.ActionStatus.Contains("QR 재시도 실패"),"a manual replay keeps its original request ID and displays the failure");
            wire.QrGetStatus=HttpStatusCode.Unauthorized;await view.RetryQrAsync("SAVE_BINDING");
            Check(view.AdminToken=="" && !view.CanSaveQr && !view.ActionStatus.Contains("QR 재시도"),"ticket authentication failure clears session-owned tracking");
            gets=wire.QrGets;view.AdminToken="new-session";await view.StartAsync();
            Check(wire.QrGets==gets,"a new session does not poll the previous session's ticket");
        }
        finally{view.Stop();}
    }
    static async Task RunQr()
    {
        using var wire=new MockServer{VideoReady=true};using var http=new HttpClient(wire);var expired=false;
        using var view=new QrRoiViewModel(new Uri("http://127.0.0.1:8000"),"test-only",()=>expired=true,new FactoryApiClient(http),canConfigure:true);
        view.Start();await Until(()=>view.QrText=="PALLET-01" && view.FullFrame is not null);
        Check(view.CanSetRoi && view.Details.Contains("CLAHE"),"live ROI and correction metadata");
        await view.SetRoiAsync([.1,.2,.5,.4]);Check(wire.Revision==1 && view.Roi[0]==.1,"ROI saves revision and normalized coordinates");
        wire.QrState="NO_FRAME";await Until(()=>view.QrText=="—");
        Check(view.FullFrame is not null,"missing decoder evidence clears the QR text while the standard JPEG is still available");
        wire.VideoReady=false;await Until(()=>view.FullFrame is null && view.RoiFrame is null);
        wire.Fail="qr-roi/status";wire.Unauthorized=true;await Until(()=>expired);Check(!view.CanSetRoi,"401 disables ROI operations");
    }
    static async Task RunReadOnlyQr()
    {
        using var wire=new MockServer{VideoReady=true};using var http=new HttpClient(wire);
        using var view=new QrRoiViewModel(new Uri("http://127.0.0.1:8000"),"test-only",()=>{},new FactoryApiClient(http));
        view.Start();await Until(()=>view.QrText=="PALLET-01" && view.RoiFrame is not null);
        Check(!view.CanSetRoi,"the ordinary admin can view the configured ROI");
        await view.SetRoiAsync([.25,0,.5,.5]);
        Check(wire.RoiWrites==0 && view.Roi.SequenceEqual(new double[]{0,0,1,1}),"the ordinary admin cannot change the configured ROI");
    }
    static async Task RunStandardCameraQr()
    {
        using var wire=new MockServer{VideoReady=true,StandardCameraOnly=true};
        using var http=new HttpClient(wire);var expired=false;
        using var view=new QrRoiViewModel(new Uri("http://127.0.0.1:8000"),"test-only",()=>expired=true,new FactoryApiClient(http));
        view.Start();
        await Until(()=>wire.MissingRoiStatus && view.FullFrame is BitmapSource{PixelWidth:8,PixelHeight:6} && view.CanSetRoi);
        await view.SetRoiAsync([.25,0,.5,.5]);
        await Until(()=>view.RoiFrame is BitmapSource{PixelWidth:4,PixelHeight:3});
        var full=(BitmapSource)view.FullFrame!;var roi=(BitmapSource)view.RoiFrame!;
        var expected=ReadPixels(full,new Int32Rect(2,0,4,3));
        var actual=ReadPixels(roi,new Int32Rect(0,0,4,3));
        Check(actual.SequenceEqual(expected) && !actual.SequenceEqual(ReadPixels(full,new Int32Rect(0,0,4,3))),
            "the preview shows pixels from the selected region of the displayed CAM1 frame");
        Check(wire.RoiWrites==0 && wire.SeenFrames.All(path=>path=="cameras/CAM1/frame"),
            "a local preview never sends an ROI write or requests another camera");
        wire.Fail="cameras/CAM1/frame";wire.Unauthorized=true;
        await Until(()=>expired);
        Check(!view.CanSetRoi,"an unauthorized standard JPEG ends the session");
    }
    static byte[] ReadPixels(BitmapSource image,Int32Rect area)
    {
        var converted=new FormatConvertedBitmap(image,PixelFormats.Bgra32,null,0);
        var stride=area.Width*4;
        var pixels=new byte[stride*area.Height];
        converted.CopyPixels(area,pixels,stride,0);
        return pixels;
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
            Check(view.CanPause,"standard plant pause stays available with a fresh plant snapshot");
            await view.PlantActionAsync("PAUSE");Check(wire.LastAction=="PAUSE","plant pause survives pallet failure");
            wire.Fail="orders";wire.Stage="TO_ASSEMBLY";wire.Units=17;await view.RefreshAsync();
            Check(view.Plant?.Stage=="TO_ASSEMBLY" && view.Stock.Single().AvailableUnits==17 && view.Orders.Single().Company=="new","order failure must not block plant or stock");
            wire.Fail="stock";wire.Company="stock-failure";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="stock-failure" && !view.CanAccept,"stock failure independent update");
            wire.Fail="plant";wire.Company="plant-failure";await view.RefreshAsync();
            Check(view.Orders.Single().Company=="plant-failure" && !view.CanStart,"plant failure independent update");
            wire.Fail="";await view.RefreshAsync();Check(view.CanAccept,"recovery unlocks only after fresh snapshots");
            wire.Fail="pallets";wire.Unauthorized=true;await view.RefreshAsync();
            Check(view.Orders.Count==0 && !view.CanAccept && !view.CanPause && view.AdminToken=="","401 must revoke the whole UI session");
        }
        finally{view.Stop();}
    }
}
sealed class MockServer:HttpMessageHandler
{
    public string Company="old",Fail="",Stage="READY",State="READY";
    public int Units=8,Posts,Revision,RoiWrites;
    public string QrState="READ";
    public double[] Roi=[0,0,1,1];
    public bool Unauthorized;
    public bool VideoReady;
    public bool MixedTrial,DropTrialReply;
    public bool DropRecoveryReply;
    public List<Guid> RecoveryRequests=[];
    public List<Guid> TrialRequests=[];
    public List<int> TrialStations=[];
    public List<string> TrialPaths=[];
    public bool AgvAccepted;
    public bool StandardCameraOnly,MissingRoiStatus;
    public bool DropQrPostReply;
    public HttpStatusCode QrGetStatus=HttpStatusCode.OK;
    public List<Guid> QrPosts=[];
    public int QrGets;
    public Guid QrTicketRead;
    public string QrTicketState="RUNNING",QrMismatch="";
    public string EnvironmentName="HARDWARE";
    public bool RequestedBench;
    public string? LastAction,LastPostPath,CycleId,FeedState,HistoricalOrderCycle,PalletCycle;
    public List<string> SeenFrames=[];
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken cancellation)
    {
        var path=request.RequestUri!.AbsolutePath.Replace("/api/v1/","");
        RequestedBench|=path.StartsWith("wireless-test/");
        if(path.EndsWith("/frame"))SeenFrames.Add(path);
        if(path=="qr-roi/roi" && request.Method==HttpMethod.Put)RoiWrites++;
        if(StandardCameraOnly && path.StartsWith("qr-roi/"))
        {
            MissingRoiStatus|=path=="qr-roi/status";
            return new HttpResponseMessage(HttpStatusCode.NotFound){Content=new StringContent("<html>Not found</html>",Encoding.UTF8,"text/html")};
        }
        object data;var status=HttpStatusCode.OK;
        if(path==Fail){status=Unauthorized?HttpStatusCode.Unauthorized:HttpStatusCode.ServiceUnavailable;data=new{code="DEVICE_UNAVAILABLE",message="mock failure",retry="QUERY_FIRST",trace_id=Guid.NewGuid()};}
        else if(path.Contains("/qr-retry"))
        {
            var parts=path.Split('/');
            var cycle=Guid.Parse(parts[1]);
            Guid id;
            if(request.Method==HttpMethod.Post)
            {
                using var body=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
                var fields=body.RootElement;id=fields.GetProperty("request_id").GetGuid();QrPosts.Add(id);Posts++;
                var previous=fields.GetProperty("previous_read_id").GetGuid();
                QrTicketRead=fields.GetProperty("action").GetString()=="SAVE_BINDING" ? previous : Guid.NewGuid();
                if(DropQrPostReply)throw new HttpRequestException("Response lost after POST");
                status=HttpStatusCode.Accepted;
            }
            else
            {
                id=Guid.Parse(parts[3]);QrGets++;status=QrGetStatus;
                if(!QrPosts.Contains(id) || cycle.ToString()!=CycleId)throw new InvalidOperationException("lookup for unknown QR ticket");
            }
            data=(int)status>=400 ? new{code="MOCK",message="ticket lookup failure",retry="QUERY_FIRST",trace_id=Guid.NewGuid()} :
                new{request_id=QrMismatch=="request"?Guid.NewGuid():id,cycle_id=QrMismatch=="cycle"?Guid.NewGuid():cycle,
                    read_id=QrMismatch=="read"?Guid.NewGuid():QrTicketRead,state=request.Method==HttpMethod.Post?"ACCEPTED":QrTicketState};
        }
        else if(path is "qr-roi/status" or "qr-roi/roi")
        {
            if(request.Method==HttpMethod.Put){var input=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));Roi=input.RootElement.GetProperty("roi").EnumerateArray().Select(v=>v.GetDouble()).ToArray();Revision++;}
            data=new{state=QrState,pallet_id=QrState=="READ"?"PALLET-01":null,text=QrState=="READ"?"PALLET-01":"",decoded_values=new[]{"PALLET-01"},method="CLAHE",frame_id="mock-frame",captured_at=DateTimeOffset.UtcNow,revision=Revision,roi=Roi,resolution=new[]{1920,1080},requested_resolution=new[]{1920,1080},requested_fps=30,capture_fps=30,decode_fps=15};
        }
        else if(path.EndsWith("/frame"))
        {
            if(VideoReady)
            {
                var width=path=="qr-roi/frame"?3:8;var height=path=="qr-roi/frame"?4:6;
                var pixels=new byte[width*height*3];
                for(var y=0;y<height;y++)
                    for(var x=0;x<width;x++)
                        Array.Fill(pixels,(byte)(x*20+y*10),(y*width+x)*3,3);
                var image=BitmapSource.Create(width,height,96,96,PixelFormats.Bgr24,null,pixels,width*3);
                var encoder=new JpegBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));using var stream=new System.IO.MemoryStream();encoder.Save(stream);
                var response=new HttpResponseMessage(HttpStatusCode.OK){Content=new ByteArrayContent(stream.ToArray())};response.Content.Headers.ContentType=new("image/jpeg");response.Headers.Add("X-Captured-At","2026-11-23T00:00:00Z");return response;
            }
            status=HttpStatusCode.ServiceUnavailable;data=new{};
        }
        else if(request.Method!=HttpMethod.Get)
        {
            Posts++;LastPostPath=path;data=new{};
            if(MixedTrial)TrialPaths.Add(path);
            if(MixedTrial && path=="manual-actions")
            {
                using var body=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
                RecoveryRequests.Add(body.RootElement.GetProperty("request_id").GetGuid());
                if(DropRecoveryReply){DropRecoveryReply=false;throw new HttpRequestException("manual reply lost");}
                data=new{command_id="00000000-0000-4000-8000-000000000099",device_id="AGV",state="ACCEPTED"};status=HttpStatusCode.Accepted;
            }
            if(MixedTrial && path=="orders")
            {
                using var body=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
                var input=body.RootElement;TrialRequests.Add(input.GetProperty("request_id").GetGuid());
                var address=input.GetProperty("address").GetString()!;
                var station=address.StartsWith("서울")?3:address.StartsWith("대전")?4:5;TrialStations.Add(station);
                if(DropTrialReply){DropTrialReply=false;throw new HttpRequestException("reply lost after trial submission");}
                data=new{order_id="00000000-0000-4000-8000-000000000001",target_station=station};status=HttpStatusCode.Created;
            }
            if(MixedTrial && path.EndsWith("/accept"))data=new{order_id="00000000-0000-4000-8000-000000000001",spec=new{target_head_id="H1",target_arm_l_id="L1",target_arm_r_id="R2"},factory_status="QUEUED"};
            if(path=="plant/actions")
            {
                using var body=JsonDocument.Parse(await request.Content!.ReadAsStringAsync(cancellation));
                LastAction=body.RootElement.GetProperty("action").GetString();
                data=new{request_id=body.RootElement.GetProperty("request_id").GetString(),state="ACCEPTED",plant_state=State};
                status=HttpStatusCode.Accepted;
            }
        }
        else if(path.StartsWith("plant/actions/"))data=new{request_id=path.Split('/').Last(),state="COMPLETED",plant_state=State};
        else if(path.StartsWith("manual-actions/"))data=new{command_id=path.Split('/').Last(),device_id="AGV",state="COMPLETED"};
        else data=path switch
        {
            "orders"=>new{orders=new[]{new{order_id="00000000-0000-4000-8000-000000000001",spec=new{target_head_id="H1",target_arm_l_id="L1",target_arm_r_id="R1"},company=Company,phone="test",address="test",target_station=3,cycle_id=HistoricalOrderCycle,pallet_id=HistoricalOrderCycle is null ? null : "PALLET-08",factory_status=HistoricalOrderCycle is null ? "PENDING" : "COMPLETED",created_at=DateTimeOffset.UtcNow,updated_at=DateTimeOffset.UtcNow}},next_cursor=(string?)null},
            "plant"=>new{state=State,stage=Stage,cycle_id=CycleId,feed_qr=FeedState is null ? null : new{read_id=Guid.Empty,attempt=3,state=FeedState},
                devices=AgvAccepted ? new object[]{new{device_id="LINE",state="IDLE",online=true},new{device_id="AGV",state="BUSY",online=true,cycle_id=CycleId,active_command_id=Guid.Empty}} : new object[]{new{device_id="LINE",state="IDLE",online=true}},
                camera_ready=new{cam1=VideoReady,cam2=VideoReady,cam3=VideoReady,cam4=VideoReady},updated_at=DateTimeOffset.UtcNow},
            "pallets"=>new{pallets=new[]{new{pallet_id="PALLET-01",cycle_id=PalletCycle,stage=Stage,updated_at=DateTimeOffset.UtcNow}},next_cursor=(string?)null},
            "stock"=>new{items=new[]{new{stock_key="ARM_1",available_units=Units}},next_cursor=(string?)null},
            _=>new{events=Array.Empty<object>(),next_cursor=(string?)null}
        };
        var result=new HttpResponseMessage(status){Content=new StringContent(JsonSerializer.Serialize(data),Encoding.UTF8,"application/json")};
        result.Headers.Add("X-Mobot-Environment",EnvironmentName);
        if(MixedTrial)result.Headers.Add("X-Mobot-Trial","LINE_AGV_MIXED_V1");return result;
    }
}
