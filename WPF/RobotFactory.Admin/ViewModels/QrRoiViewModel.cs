using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Media.Imaging;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.ViewModels;

public sealed class QrRoiViewModel : INotifyPropertyChanged, IDisposable
{
    private readonly FactoryApiClient api;
    private readonly Uri server;
    private readonly string token;
    private readonly Action expired;
    private readonly bool canConfigure;
    private readonly CancellationTokenSource lifetime=new();
    private readonly CancellationToken cancellation;
    private Task loops=Task.CompletedTask;
    private bool started,saving,disposed;
    private QrRoiV1? status;
    private bool previewOnly;
    private double[] previewRoi = [0,0,1,1];
    private string? statusMessage, statusError, frameError;
    public BitmapSource? FullFrame {get;private set;}
    public BitmapSource? RoiFrame {get;private set;}
    public string Message => frameError ?? statusError ?? statusMessage ?? "";
    public string SelectionHint => previewOnly ? "드래그로 이 창의 확대 영역 선택"
        : canConfigure ? "드래그로 서버 판독 영역 설정" : "서버에 설정된 판독 영역";
    public string QrText=>status is {State:"READ" or "INVALID_QR"}?status.Text:status?.State=="MULTIPLE_QR"?string.Join("\n",status.DecodedValues):"—";
    public string StateText=>previewOnly ? "서버 실시간 판독 정보 미제공" : status?.State switch {"READ"=>"QR 인식됨","INVALID_QR"=>"팔레트 QR 형식 아님","MULTIPLE_QR"=>"QR 여러 개 감지","UNREADABLE"=>"QR 미인식","WAITING"=>"판독 대기","NO_FRAME"=>"카메라 영상 없음",_=>"판독 상태 확인 중"};
    public string Details=>status is null?"—":$"실제 해상도 {string.Join(" × ",status.Resolution)}\n수신 {status.CaptureFps:0.0} fps · QR {status.DecodeFps:0.0} fps\n요청 {string.Join(" × ",status.RequestedResolution)} · {status.RequestedFps:0} fps\n판독: {(status.Method=="CLAHE"?"원본 실패 → CLAHE 명암 보정":status.Method=="ORIGINAL"?"원본 ROI":"대기")}\n촬영 {status.CapturedAt?.ToLocalTime():HH:mm:ss}";
    public IReadOnlyList<double> Roi=>previewOnly || status is null ? previewRoi : status.Roi;
    public bool CanSetRoi=>!saving && !cancellation.IsCancellationRequested && (previewOnly ? FullFrame is not null
        : canConfigure && status is not null && status.Resolution[0]>0);
    public QrRoiViewModel(Uri server,string token,Action expired,FactoryApiClient? api=null,bool canConfigure=false)
    {cancellation=lifetime.Token;this.server=server;this.token=token;this.expired=expired;this.api=api ?? new();this.canConfigure=canConfigure;}
    public void Start(){if(started)return;started=true;loops=Task.WhenAll(PollStatus(),PollFrames());}
    private bool SessionEnded(Exception error)
    {
        if(cancellation.IsCancellationRequested)return true;
        if(error is not HttpRequestException{StatusCode:HttpStatusCode.Unauthorized})return false;
        lifetime.Cancel();status=null;SetFrame(null);NotifyAll();expired();return true;
    }
    private async Task PollStatus()
    {
        while(!lifetime.IsCancellationRequested)
        {
            try
            {
                var value=await api.RequestAsync<QrRoiV1>(server,token,"qr-roi/status",null,cancellation);
                cancellation.ThrowIfCancellationRequested();
                ApplyStatus(value);
            }
            catch(HttpRequestException error)when(error.StatusCode is HttpStatusCode.NotFound or HttpStatusCode.MethodNotAllowed or HttpStatusCode.Gone)
            {
                if(cancellation.IsCancellationRequested)return;
                previewOnly=true;status=null;statusError=null;statusMessage="확대 영역은 이 창에만 적용됩니다. 회차의 QR 배정은 관제 화면에서 확인하세요.";
                UpdateCrop();NotifyAll();return;
            }
            catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException)
            {
                if(SessionEnded(error))return;
                status=null;statusError="판독 상태 조회 실패 · "+error.Message;UpdateCrop();NotifyAll();
            }
            try{await Task.Delay(200,cancellation);}catch(OperationCanceledException){break;}
        }
    }
    private async Task PollFrames()
    {
        string? fullStamp=null;
        while(!lifetime.IsCancellationRequested)
        {
            var clock=Stopwatch.StartNew();
            try
            {
                var frame=await api.LiveFrameAsync(server,token,"cam1",cancellation);
                cancellation.ThrowIfCancellationRequested();
                var priorError=frameError;
                if(frame is null)
                {
                    if(FullFrame is not null)SetFrame(null);
                    frameError="CAM 01 영상 수신 대기";
                }
                else if(FullFrame is null || frame.CapturedAt is null || frame.CapturedAt!=fullStamp)
                {
                    var image=await Task.Run(()=>Decode(frame.Bytes),cancellation);
                    cancellation.ThrowIfCancellationRequested();
                    SetFrame(image);frameError=null;
                }
                fullStamp=frame?.CapturedAt;
                if(priorError!=frameError)Notify(nameof(Message));
            }
            catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException or NotSupportedException or FormatException or COMException)
            {if(SessionEnded(error))return;SetFrame(null);fullStamp=null;frameError=error.Message;Notify(nameof(Message));}
            var delay=FullFrame is null ? 250 : Math.Max(1,33-(int)clock.ElapsedMilliseconds);
            try{await Task.Delay(delay,cancellation);}catch(OperationCanceledException){break;}
        }
    }
    private static BitmapSource Decode(byte[] bytes)
    {
        using var stream=new MemoryStream(bytes);var bitmap=new BitmapImage();bitmap.BeginInit();bitmap.CacheOption=BitmapCacheOption.OnLoad;bitmap.StreamSource=stream;bitmap.EndInit();bitmap.Freeze();return bitmap;
    }
    private static bool ValidRoi(IReadOnlyList<double>? roi)=>roi is {Count:4} && roi.All(double.IsFinite)
        && roi[0]>=0 && roi[1]>=0 && roi[2]>=.01 && roi[3]>=.01 && roi[0]+roi[2]<=1.000001 && roi[1]+roi[3]<=1.000001;
    private void ApplyStatus(QrRoiV1 value)
    {
        if(!ValidRoi(value.Roi) || value.Resolution?.Count!=2 || value.RequestedResolution?.Count!=2 || value.DecodedValues is null)
            throw new JsonException("ROI 상태 형식 오류");
        if(status is not null && value.Revision<status.Revision)return;
        var areaChanged=status is null || !value.Roi.SequenceEqual(status.Roi);
        status=value;statusError=null;
        if(areaChanged)UpdateCrop();
        NotifyAll();
    }
    private void SetFrame(BitmapSource? image)
    {
        bool availabilityChanged=(FullFrame is null)!=(image is null);
        FullFrame=image;Notify(nameof(FullFrame));UpdateCrop();
        if(availabilityChanged)Notify(nameof(CanSetRoi));
    }
    private void UpdateCrop()
    {
        RoiFrame=null;
        if(FullFrame is {} image && (previewOnly || status is not null))
        {
            int width=image.PixelWidth,height=image.PixelHeight;
            var roi=Roi;
            int x=Math.Clamp((int)(width*roi[0]),0,width-1),y=Math.Clamp((int)(height*roi[1]),0,height-1);
            int right=Math.Clamp((int)Math.Round(width*(roi[0]+roi[2])),x+1,width);
            int bottom=Math.Clamp((int)Math.Round(height*(roi[1]+roi[3])),y+1,height);
            if(x==0 && y==0 && right==width && bottom==height)RoiFrame=image;
            else {var crop=new CroppedBitmap(image,new Int32Rect(x,y,right-x,bottom-y));crop.Freeze();RoiFrame=crop;}
        }
        Notify(nameof(RoiFrame));
    }
    public async Task SetRoiAsync(double[] roi)
    {
        if(!CanSetRoi || !ValidRoi(roi))return;
        if(previewOnly){previewRoi=roi.ToArray();UpdateCrop();Notify(nameof(Roi));return;}
        if(status is null)return;
        saving=true;Notify(nameof(CanSetRoi));
        var revision=status.Revision;
        try
        {
            var value=await api.RequestAsync<QrRoiV1>(server,token,"qr-roi/roi",new{roi,expected_revision=revision},cancellation,HttpMethod.Put);
            cancellation.ThrowIfCancellationRequested();
            ApplyStatus(value);statusMessage="ROI 저장 완료";Notify(nameof(Message));
        }
        catch(Exception error)when(error is HttpRequestException or IOException or JsonException or OperationCanceledException){if(!SessionEnded(error)){statusMessage=error.Message;Notify(nameof(Message));}}
        finally{saving=false;Notify(nameof(CanSetRoi));}
    }
    private void NotifyAll(){foreach(var n in new[]{nameof(Message),nameof(QrText),nameof(StateText),nameof(Details),nameof(Roi),nameof(SelectionHint),nameof(CanSetRoi)})Notify(n);}
    public void Dispose(){if(disposed)return;disposed=true;lifetime.Cancel();_ = DisposeAfterLoops();}
    private async Task DisposeAfterLoops(){try{await loops;}catch(OperationCanceledException){}finally{lifetime.Dispose();}}
    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName]string? name=null)=>PropertyChanged?.Invoke(this,new(name));
}
