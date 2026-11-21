using System.Windows;
using System.Diagnostics;
using System.Windows.Interop;
using System.Runtime.InteropServices;
using RobotFactory.Admin.ViewModels;
namespace RobotFactory.Admin;
public partial class MainWindow : Window
{
    private HwndSource? windowSource;
    private double clientAspectRatio = 2000.0 / 1056.0;
    private NativeRect resizeStart;

    public MainWindow()
    {
        InitializeComponent();
        DataContext = new MainViewModel();
    }
    private async void Logout_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel model) await model.LogoutAsync();
    }

    private async void Pause_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel { CurrentView: DashboardViewModel dashboard }) await dashboard.PlantActionAsync("PAUSE");
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        windowSource = HwndSource.FromHwnd(new WindowInteropHelper(this).Handle);
        windowSource?.AddHook(ConstrainAspectRatio);
    }

    protected override void OnContentRendered(EventArgs e)
    {
        if (WindowState == WindowState.Maximized && windowSource is not null &&
            GetClientRect(windowSource.Handle, out var client) && client.Right > 0 && client.Bottom > 0)
            clientAspectRatio = (double)client.Right / client.Bottom;
        base.OnContentRendered(e);
    }

    protected override void OnClosed(EventArgs e)
    {
        if (windowSource is { IsDisposed: false }) windowSource.RemoveHook(ConstrainAspectRatio);
        windowSource = null;
        base.OnClosed(e);
    }

    private IntPtr ConstrainAspectRatio(IntPtr handle, int message, IntPtr edgePointer, IntPtr rectanglePointer, ref bool handled)
    {
        try
        {
            const int enterSizeMove = 0x0231;
            const int sizing = 0x0214;
            if (message == enterSizeMove) GetWindowRect(handle, out resizeStart);
            long edgeValue = edgePointer.ToInt64();
            if (message != sizing || WindowState != WindowState.Normal || rectanglePointer == IntPtr.Zero ||
                edgeValue < 1 || edgeValue > 8 || !double.IsFinite(clientAspectRatio) || clientAspectRatio <= 0)
                return IntPtr.Zero;
            if (!GetWindowRect(handle, out var window) || !GetClientRect(handle, out var client) ||
                client.Right <= 0 || client.Bottom <= 0)
                return IntPtr.Zero;

            var rectangle = Marshal.PtrToStructure<NativeRect>(rectanglePointer);
            int edge = (int)edgeValue;
            long frameWidth = (long)window.Right - window.Left - client.Right;
            long frameHeight = (long)window.Bottom - window.Top - client.Bottom;
            long width = (long)rectangle.Right - rectangle.Left;
            long height = (long)rectangle.Bottom - rectangle.Top;
            if (frameWidth < 0 || frameHeight < 0 || width <= frameWidth || height <= frameHeight)
                return IntPtr.Zero;
            double widthChange = Math.Abs(width - ((long)resizeStart.Right - resizeStart.Left));
            double heightChange = Math.Abs(height - ((long)resizeStart.Bottom - resizeStart.Top)) * clientAspectRatio;
            bool widthDriven = edge is 1 or 2 || ((edge is 4 or 5 or 7 or 8) && widthChange >= heightChange);

            // WM_SIZING edge values identify which opposite corner stays anchored.
            if (widthDriven)
                height = checked((long)Math.Round((width - frameWidth) / clientAspectRatio) + frameHeight);
            else
                width = checked((long)Math.Round((height - frameHeight) * clientAspectRatio) + frameWidth);
            if (width <= frameWidth || height <= frameHeight || width > int.MaxValue || height > int.MaxValue)
                return IntPtr.Zero;
            if (edge is 1 or 4 or 7) rectangle.Left = checked((int)(rectangle.Right - width));
            else rectangle.Right = checked((int)(rectangle.Left + width));
            if (edge is 3 or 4 or 5) rectangle.Top = checked((int)(rectangle.Bottom - height));
            else rectangle.Bottom = checked((int)(rectangle.Top + height));

            Marshal.StructureToPtr(rectangle, rectanglePointer, false);
            handled = true;
            return new IntPtr(1);
        }
        catch (Exception exception) when (exception is ArgumentException or ArithmeticException or InvalidOperationException or ExternalException)
        {
            Trace.TraceError($"Window resize failed: {exception}");
            return IntPtr.Zero;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct NativeRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(IntPtr handle, out NativeRect rectangle);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetClientRect(IntPtr handle, out NativeRect rectangle);
}
