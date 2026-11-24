using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed class QrRoiViewModel : INotifyPropertyChanged, IDisposable
{
    private readonly FactoryApiClient api;
    private readonly Uri server;
    private readonly string token;
    private readonly Action expired;
    private readonly CancellationTokenSource lifetime=new();
    private readonly CancellationToken cancellation;
    private Task loops=Task.CompletedTask;
    private bool started,saving,disposed;
    private QrRoiV1? status;
    public ImageSource? FullFrame {get;private set;}
    public ImageSource? RoiFrame {get;private set;}
    public string Message {get;private set;}="서버 CAM1 연결 중";
    public string QrText=>status is {State:"READ" or "INVALID_QR"}?status.Text:status?.State=="MULTIPLE_QR"?string.Join("\n",status.DecodedValues):"—";
    public string StateText=>status?.State switch {"READ"=>"QR 인식됨","INVALID_QR"=>"팔레트 QR 형식 아님","MULTIPLE_QR"=>"QR 여러 개 감지","UNREADABLE"=>"QR 미인식","WAITING"=>"판독 대기",_=>"카메라 영상 없음"};
    public string Details=>status is null?"—":$"실제 해상도 {string.Join(" × ",status.Resolution)}\n수신 {status.CaptureFps:0.0} fps · QR {status.DecodeFps:0.0} fps\n요청 {string.Join(" × ",status.RequestedResolution)} · {status.RequestedFps:0} fps\n판독: {(status.Method=="CLAHE"?"원본 실패 → CLAHE 명암 보정":status.Method=="ORIGINAL"?"원본 ROI":"대기")}\n촬영 {status.CapturedAt?.ToLocalTime():HH:mm:ss}";
    public IReadOnlyList<double> Roi=>status?.Roi ?? new List<double>{0,0,1,1};
    public IReadOnlyList<int> Resolution=>status?.Resolution ?? new List<int>{0,0};
    public bool CanSetRoi=>!saving && status is not null && status.Resolution.Count==2 && status.Resolution[0]>0;
    public QrRoiViewModel(Uri server,string token,Action expired,FactoryApiClient? api=null){cancellation=lifetime.Token;this.server=server;this.token=token;this.expired=expired;this.api=api ?? new();}
    public void Start(){if(started)return;started=true;loops=Task.WhenAll(PollStatus(),PollFrames());}
    private bool Handle(Exception error)
    {
        if(lifetime.IsCancellationRequested)return false;
        Message=error.Message;NotifyAll();
        if(error is HttpRequestException{StatusCode:HttpStatusCode.Unauthorized}){lifetime.Cancel();expired();return false;}
        return true;
    }
    private async Task PollStatus()
    {
        while(!lifetime.IsCancellationRequested)
        {
            try
            {
                var value=await api.RequestAsync<QrRoiV1>(server,token,"qr-roi/status",null,cancellation);
                cancellation.ThrowIfCancellationRequested();
                if(value.Roi is null || value.Roi.Count!=4 || value.Resolution is null || value.Resolution.Count!=2 || value.DecodedValues is null)throw new JsonException("ROI 상태 형식 오류");
                if(status is not null && value.Revision<status.Revision)continue;
                status=value;Message="실시간 판독 · 화면 확인만으로 주문·모터가 동작하지 않습니다.";NotifyAll();
                if(value.State=="NO_FRAME"){FullFrame=RoiFrame=null;Notify(nameof(FullFrame));Notify(nameof(RoiFrame));}
            }
            catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
            {
                status=null;FullFrame=RoiFrame=null;Notify(nameof(FullFrame));Notify(nameof(RoiFrame));NotifyAll();
                if(!Handle(error))break;
            }
            try{await Task.Delay(200,cancellation);}catch(OperationCanceledException){break;}
        }
    }
    private async Task PollFrames()
    {
        string? fullStamp=null,roiStamp=null;
        while(!lifetime.IsCancellationRequested)
        {
            var clock=Stopwatch.StartNew();
            try
            {
                var full=api.LiveFrameAsync(server,token,"cam1",cancellation);
                var roi=api.RoiFrameAsync(server,token,cancellation);
                await Task.WhenAll(full,roi);cancellation.ThrowIfCancellationRequested();
                var f=await full;var r=await roi;
                var changedFull=f is not null && (f.CapturedAt is null || f.CapturedAt!=fullStamp);
                var changedRoi=r is not null && (r.CapturedAt is null || r.CapturedAt!=roiStamp);
                var images=await Task.Run(()=>new[]{changedFull?Decode(f!.Bytes):null,changedRoi?Decode(r!.Bytes):null},cancellation);
                cancellation.ThrowIfCancellationRequested();
                if(changedFull || f is null){FullFrame=images[0];Notify(nameof(FullFrame));}fullStamp=f?.CapturedAt;
                if(changedRoi || r is null){RoiFrame=images[1];Notify(nameof(RoiFrame));}roiStamp=r?.CapturedAt;
            }
            catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException or NotSupportedException or FormatException or COMException)
            {FullFrame=RoiFrame=null;fullStamp=roiStamp=null;Notify(nameof(FullFrame));Notify(nameof(RoiFrame));if(!Handle(error))break;}
            try{await Task.Delay(Math.Max(1,33-(int)clock.ElapsedMilliseconds),cancellation);}catch(OperationCanceledException){break;}
        }
    }
    private static ImageSource Decode(byte[] bytes)
    {
        using var stream=new MemoryStream(bytes);var bitmap=new BitmapImage();bitmap.BeginInit();bitmap.CacheOption=BitmapCacheOption.OnLoad;bitmap.StreamSource=stream;bitmap.EndInit();bitmap.Freeze();return bitmap;
    }
    public async Task SetRoiAsync(double[] roi)
    {
        if(!CanSetRoi || status is null)return;saving=true;Notify(nameof(CanSetRoi));
        var revision=status.Revision;
        try
        {
            var value=await api.RequestAsync<QrRoiV1>(server,token,"qr-roi/roi",new{roi,expected_revision=revision},cancellation,HttpMethod.Put);
            cancellation.ThrowIfCancellationRequested();status=value;Message="ROI 저장 완료 · 새 영역으로 판독 중";NotifyAll();
        }
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException){Handle(error);}
        finally{saving=false;Notify(nameof(CanSetRoi));}
    }
    private void NotifyAll(){foreach(var n in new[]{nameof(Message),nameof(QrText),nameof(StateText),nameof(Details),nameof(Roi),nameof(Resolution),nameof(CanSetRoi)})Notify(n);}
    public void Dispose(){if(disposed)return;disposed=true;lifetime.Cancel();_ = DisposeAfterLoops();}
    private async Task DisposeAfterLoops(){try{await loops;}catch(OperationCanceledException){}finally{lifetime.Dispose();}}
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName]string? name=null)=>PropertyChanged?.Invoke(this,new(name));
}
