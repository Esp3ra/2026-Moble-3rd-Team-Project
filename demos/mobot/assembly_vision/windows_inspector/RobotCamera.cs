using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Security.Cryptography;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.ML.OnnxRuntime;
using Microsoft.ML.OnnxRuntime.Tensors;
using Windows.Graphics.Imaging;
using Windows.Media.Capture;
using Windows.Media.Capture.Frames;
using Windows.Media.MediaProperties;

class Detector : IDisposable
{
    const int Size = 640;
    readonly InferenceSession session;
    readonly string inputName;
    readonly int candidateCount;
    public readonly string[] Names;

    public Detector()
    {
        string dir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "model");
        string modelPath = Path.Combine(dir, "best.onnx");
        string classesPath = Path.Combine(dir, "classes.txt");
        var missing = new[] { modelPath, classesPath }.Where(path => !File.Exists(path)).ToArray();
        if (missing.Length > 0)
            throw new FileNotFoundException("ZIP을 모두 압축 해제한 뒤 아래 파일을 확인하세요.\n" + String.Join("\n", missing));

        Names = File.ReadAllLines(classesPath);
        if (Names.Length == 0 || Names.Any(String.IsNullOrWhiteSpace))
            throw new InvalidDataException("model\\classes.txt에 클래스 이름을 한 줄씩 넣어 주세요.");

        using (var options = new SessionOptions())
            session = new InferenceSession(modelPath, options);
        try
        {
            var input = session.InputMetadata.Single();
            var output = session.OutputMetadata.Single().Value;
            if (input.Value.ElementType != typeof(float) ||
                !input.Value.Dimensions.SequenceEqual(new[] { 1, 3, Size, Size }) ||
                output.ElementType != typeof(float) ||
                output.Dimensions.Length != 3 || output.Dimensions[0] != 1 ||
                output.Dimensions[1] != 4 + Names.Length || output.Dimensions[2] <= 0)
                throw new InvalidDataException("model\\best.onnx에 640×640 고정 크기의 YOLO 검출 모델을 넣고 클래스 목록을 확인하세요.");

            inputName = input.Key;
            candidateCount = output.Dimensions[2];
        }
        catch
        {
            session.Dispose();
            throw;
        }
    }

    public unsafe List<Detection> Predict(Bitmap photo)
    {
        float scale = Math.Min((float)Size / photo.Width, (float)Size / photo.Height);
        int w = (int)Math.Round(photo.Width * scale), h = (int)Math.Round(photo.Height * scale);
        int left = (Size - w) / 2, top = (Size - h) / 2;
        var input = new float[3 * Size * Size];
        using (var square = new Bitmap(Size, Size, PixelFormat.Format24bppRgb))
        {
            using (var g = Graphics.FromImage(square))
            {
                g.Clear(Color.FromArgb(114, 114, 114));
                g.InterpolationMode = InterpolationMode.Bilinear;
                g.PixelOffsetMode = PixelOffsetMode.Half;
                g.DrawImage(photo, new Rectangle(left, top, w, h), 0, 0, photo.Width, photo.Height, GraphicsUnit.Pixel);
            }
            var pixels = square.LockBits(new Rectangle(0, 0, Size, Size), ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
            try
            {
                for (int y = 0; y < Size; y++)
                {
                    byte* row = (byte*)pixels.Scan0 + y * pixels.Stride;
                    for (int x = 0; x < Size; x++)
                    {
                        int i = y * Size + x;
                        input[i] = row[x * 3 + 2] / 255f;
                        input[i + Size * Size] = row[x * 3 + 1] / 255f;
                        input[i + 2 * Size * Size] = row[x * 3] / 255f;
                    }
                }
            }
            finally { square.UnlockBits(pixels); }
        }
        var tensor = new DenseTensor<float>(input, new[] { 1, 3, Size, Size });
        using (var result = session.Run(new[] { NamedOnnxValue.CreateFromTensor(inputName, tensor) }))
        {
            int count = candidateCount;
            var scores = result.Single().AsTensor<float>().ToArray();
            var candidates = new List<Detection>();
            for (int i = 0; i < count; i++)
            {
                int cls = 0;
                for (int c = 1; c < Names.Length; c++)
                    if (scores[(4+c)*count+i] > scores[(4+cls)*count+i]) cls = c;
                float score = scores[(4+cls)*count+i];
                if (score < 0.25f) continue;
                float cx = scores[i], cy = scores[count+i];
                float bw = scores[2*count+i], bh = scores[3*count+i];
                var box = new RectangleF((cx-bw/2-left)/scale, (cy-bh/2-top)/scale, bw/scale, bh/scale);
                candidates.Add(new Detection {ClassId=cls, Score=score, Box=box});
            }
            var keep = new List<Detection>();
            foreach (var d in candidates.OrderByDescending(p => p.Score))
            {
                if (keep.Any(k => k.ClassId == d.ClassId && Overlap(k.Box, d.Box) > 0.7f)) continue;
                keep.Add(d);
                if (keep.Count == 300) break;
            }
            foreach (var d in keep) d.Box = RectangleF.Intersect(d.Box, new RectangleF(0,0,photo.Width,photo.Height));
            return keep;
        }
    }

    static float Overlap(RectangleF a, RectangleF b)
    {
        var r = RectangleF.Intersect(a, b);
        float common = r.Width * r.Height;
        return common / Math.Max(0.0001f, a.Width*a.Height + b.Width*b.Height - common);
    }
    public void Dispose() { session.Dispose(); }
}

partial class CameraWindow : Form
{
    readonly ComboBox cameras = new ComboBox { Width=330, DropDownStyle=ComboBoxStyle.DropDownList };
    readonly Button start = new Button { Text="검출 시작", Enabled=false, AutoSize=true };
    readonly Button rotate = new Button { Text="90° 회전", AutoSize=true };
    readonly Label state = new Label { AutoSize=true, Padding=new Padding(6) };
    readonly PictureBox preview = new PictureBox { Dock=DockStyle.Fill, SizeMode=PictureBoxSizeMode.Zoom, BackColor=Color.Black };
    readonly ListBox details = new ListBox { Dock=DockStyle.Right, Width=265 };
    bool running, closing;
    int angle;

    public CameraWindow()
    {
        Text = "조립 직후 부품 검사"; ClientSize = new Size(1100, 680); MinimumSize = new Size(900, 500);
        var bar = new FlowLayoutPanel { Dock=DockStyle.Top, Height=40 };
        bar.Controls.AddRange(new Control[] {cameras, start, rotate, state});
        Controls.Add(preview); Controls.Add(details); Controls.Add(bar);
        AddInspectionControls(bar);
        rotate.Click += delegate {
            angle = (angle+1)%4;
            ResetRegions();
            inspectionStatus.Text = "회전한 영상에 맞춰 검사 영역을 다시 지정하세요.";
        };
        start.Click += async delegate { await RunCamera(); };
        Shown += async delegate {
            try {
                var devices = await MediaFrameSourceGroup.FindAllAsync();
                if (IsDisposed) return;
                cameras.DisplayMember = "DisplayName";
                foreach (var device in devices.Where(g => g.SourceInfos.Any(s => s.SourceKind == MediaFrameSourceKind.Color)))
                    cameras.Items.Add(device);
                if (cameras.Items.Count == 0) { state.Text="연결된 컬러 웹캠이 없습니다."; return; }
                cameras.SelectedIndex=0; start.Enabled=true;
            } catch (Exception ex) { if (!IsDisposed) state.Text=ex.Message; }
        };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (running) { closing=true; e.Cancel=true; }
        };
        FormClosed += delegate { if (preview.Image != null) preview.Image.Dispose(); };
    }

    static Bitmap CopyFrame(SoftwareBitmap raw, bool sample, out string fingerprint)
    {
        fingerprint = null;
        using (var bgra = SoftwareBitmap.Convert(raw, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore))
        {
            int w=bgra.PixelWidth, h=bgra.PixelHeight;
            var bytes = new byte[w*h*4];
            bgra.CopyToBuffer(bytes.AsBuffer());
            if (sample)
                using (var sha = SHA256.Create()) fingerprint = Convert.ToBase64String(sha.ComputeHash(bytes));
            var bitmap = new Bitmap(w,h,PixelFormat.Format32bppRgb);
            var data = bitmap.LockBits(new Rectangle(0,0,w,h), ImageLockMode.WriteOnly, bitmap.PixelFormat);
            try { Marshal.Copy(bytes,0,data.Scan0,bytes.Length); }
            finally { bitmap.UnlockBits(data); }
            return bitmap;
        }
    }

    async Task RunCamera()
    {
        running=true; start.Enabled=false; cameras.Enabled=false;
        var previousImage = preview.Image;
        preview.Image = null;
        if (previousImage != null) previousImage.Dispose();
        ResetRegions();
        inspectionStatus.Text = "카메라를 연 뒤 검사 영역과 주문 부품을 선택하세요.";
        string stage = "모델 불러오기";
        try {
            state.Text="모델 불러오는 중…";
            using (var detector = await Task.Run(() => new Detector()))
            using (var capture = new MediaCapture())
            {
                if (closing) return;
                InspectionEngine.MapClasses(detector.Names);
                stage = "카메라 연결";
                state.Text="카메라 연결 중…";
                var device = (MediaFrameSourceGroup)cameras.SelectedItem;
                await capture.InitializeAsync(new MediaCaptureInitializationSettings {
                    SourceGroup=device, StreamingCaptureMode=StreamingCaptureMode.Video,
                    MemoryPreference=MediaCaptureMemoryPreference.Cpu, SharingMode=MediaCaptureSharingMode.SharedReadOnly
                });
                var source = capture.FrameSources.Values.Where(s => s.Info.SourceKind == MediaFrameSourceKind.Color)
                    .OrderByDescending(s => s.Info.MediaStreamType == MediaStreamType.VideoPreview).FirstOrDefault();
                if (source == null) throw new IOException("이 카메라에서 컬러 영상 스트림을 열 수 없습니다.");
                using (var reader = await capture.CreateFrameReaderAsync(source, MediaEncodingSubtypes.Bgra8))
                {
                    object sync = new object();
                    SoftwareBitmap latest = null;
                    bool accepting = true;
                    Windows.Foundation.TypedEventHandler<MediaFrameReader,MediaFrameArrivedEventArgs> arrived = delegate(MediaFrameReader sender, MediaFrameArrivedEventArgs args) {
                        lock (sync) {
                            if (!accepting) return;
                            using (var frame = sender.TryAcquireLatestFrame()) {
                                var next = frame == null || frame.VideoMediaFrame == null ? null : frame.VideoMediaFrame.SoftwareBitmap;
                                if (next == null) return;
                                if (latest != null) latest.Dispose();
                                latest = next;
                            }
                        }
                    };
                    reader.FrameArrived += arrived;
                    try {
                        var status = await reader.StartAsync();
                        if (status != MediaFrameReaderStartStatus.Success) throw new IOException("카메라 시작 오류: "+status);
                        stage = "영상 검출";
                        state.Text="영상 대기 중…";
                        DateTime lastFrame=DateTime.UtcNow;
                        while (!closing) {
                            bool sample = ShouldSample();
                            SoftwareBitmap pending;
                            lock (sync) { pending=latest; latest=null; }
                            using (var raw = pending) {
                                if (raw == null) {
                                    if ((DateTime.UtcNow-lastFrame).TotalSeconds > 8)
                                    {
                                        if (request != null) FinishIncompleteCapture();
                                        throw new IOException("영상이 없습니다. 카메라 연결 및 Windows 카메라 권한을 확인하세요.");
                                    }
                                    await Task.Delay(25); continue;
                                }
                                {
                                    string fingerprint;
                                    var bitmap = CopyFrame(raw, sample, out fingerprint);
                                    try {
                                        bitmap.RotateFlip((RotateFlipType)angle);
                                        var found = await Task.Run(() => detector.Predict(bitmap));
                                        Draw(bitmap,found,detector.Names);
                                        var old = preview.Image; preview.Image=bitmap; bitmap=null;
                                        if (old != null) old.Dispose();
                                        if (!closing && fingerprint != null && request != null)
                                        {
                                            preview.Refresh();
                                            RecordSample((Bitmap)preview.Image, found, fingerprint, detector.Names);
                                        }
                                        UpdateInspectionControls();
                                        state.Text="검출 "+found.Count+"개"; lastFrame=DateTime.UtcNow;
                                    } finally { if (bitmap != null) bitmap.Dispose(); }
                                }
                            }
                            await Task.Delay(1);
                        }
                        await reader.StopAsync();
                    } finally {
                        lock (sync) { accepting=false; if (latest!=null) latest.Dispose(); }
                        reader.FrameArrived -= arrived;
                    }
                }
            }
        } catch (Exception ex) {
            if (!closing) MessageBox.Show(
                String.Format("{0} 중 오류가 발생했습니다.\n\n{1}", stage, Program.DescribeError(ex)),
                "카메라 검출 오류");
        }
        finally {
            running=false; cameras.Enabled=true; start.Enabled=true;
            request = null;
            UpdateInspectionControls();
            if (closing) Close(); else state.Text="카메라를 선택하고 다시 시작하세요.";
        }
    }

    void Draw(Bitmap bitmap, List<Detection> found, string[] names)
    {
        details.Items.Clear();
        using (var g=Graphics.FromImage(bitmap))
        using (var pen=new Pen(Color.LimeGreen,3))
        using (var font=new Font(FontFamily.GenericSansSerif,16,FontStyle.Bold))
        {
            for (int i=0;i<found.Count;i++) {
                var d=found[i]; var b=d.Box;
                g.DrawRectangle(pen,b.X,b.Y,b.Width,b.Height);
                string id=(i+1).ToString();
                var size=g.MeasureString(id,font);
                g.FillRectangle(Brushes.Black,b.X,b.Y,size.Width,size.Height);
                g.DrawString(id,font,Brushes.White,b.X,b.Y);
                details.Items.Add(String.Format("{0}. {1}  {2:F2}",id,names[d.ClassId],d.Score));
            }
        }
    }
}

static class Program
{
    internal static string ResultJson(InspectionResult result)
    {
        return new JavaScriptSerializer().Serialize(result);
    }

    static void InspectImages(string[] args)
    {
        const string confirmOption = "--confirm-visible";
        var imageArguments = args.Skip(5).ToArray();
        bool visibilityConfirmed = imageArguments.Contains(confirmOption);
        var imagePaths = imageArguments.Where(argument => argument != confirmOption).ToArray();
        if (imagePaths.Length == 0 || imagePaths.Any(path => path.StartsWith("--", StringComparison.Ordinal)))
            throw new ArgumentException("--inspect-images regions.json H1 L1 R2 image1.jpg [image2.jpg image3.jpg] [--confirm-visible]");
        var rects = new JavaScriptSerializer().Deserialize<float[][]>(File.ReadAllText(args[1]));
        if (rects == null || rects.Any(rect => rect == null || rect.Length != 4))
            throw new ArgumentException("regions.json에 [left, top, right, bottom] 영역 3개를 넣으세요.");
        var regions = rects.Select(rect => RectangleF.FromLTRB(rect[0], rect[1], rect[2], rect[3])).ToArray();
        var expected = args.Skip(2).Take(3).ToArray();
        var frames = new List<InspectionFrame>();
        using (var detector = new Detector())
        using (var sha = SHA256.Create())
        {
            foreach (string path in imagePaths)
            {
                using (var image = new Bitmap(path))
                using (var pixels = new MemoryStream())
                {
                    image.Save(pixels, ImageFormat.Png);
                    var fingerprint = Convert.ToBase64String(sha.ComputeHash(pixels.ToArray()));
                    frames.Add(new InspectionFrame { Size = image.Size, Fingerprint = fingerprint, Hits = detector.Predict(image) });
                }
            }
            Console.WriteLine(ResultJson(InspectionEngine.Evaluate(frames, regions, expected, detector.Names, visibilityConfirmed)));
        }
    }

    internal static string DescribeError(Exception error)
    {
        Exception cause = error.GetBaseException();
        return String.Format("{0}\n{1} (0x{2:X8})", cause.Message, cause.GetType().Name, cause.HResult);
    }

    [STAThread]
    static int Main(string[] args)
    {
        try {
            if (args.Length > 0 && args[0] == "--inspect-images") {
                InspectImages(args);
                return 0;
            }
            if (args.Length == 1 && args[0] == "--check-model") {
                using (var detector = new Detector())
                using (var image = new Bitmap(640, 640)) {
                    detector.Predict(image);
                    Console.WriteLine("ONNX OK | {0} classes", detector.Names.Length);
                }
                return 0;
            }
            if (args.Length==2 && args[0]=="--image") {
                using (var detector=new Detector())
                using (var image=new Bitmap(args[1]))
                    foreach (var d in detector.Predict(image))
                        Console.WriteLine("{0} {1:F3} {2}",detector.Names[d.ClassId],d.Score,d.Box);
                return 0;
            }
            Application.EnableVisualStyles();
            Application.Run(new CameraWindow());
            return 0;
        } catch (Exception ex) { Console.Error.WriteLine(DescribeError(ex)); return 1; }
    }
}
