using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using RobotFactory.Admin.ViewModels;
using RobotFactory.Admin.Services;

namespace RobotFactory.Admin.Views;
public partial class QrRoiView:UserControl
{
    private QrRoiViewModel? model;
    private Point? start;
    private double[]? selection;
    public QrRoiView()
    {
        InitializeComponent();
        Loaded+=(_,_)=>{model=DataContext as QrRoiViewModel;if(model is not null)model.PropertyChanged+=Changed;Draw();};
        Unloaded+=(_,_)=>{if(model is not null)model.PropertyChanged-=Changed;model=null;};
    }
    private void Changed(object? sender,PropertyChangedEventArgs e){if(e.PropertyName is "Roi" or "FullFrame")Draw();}
    private Rect Bounds()
    {
        if(FullImage.Source is not BitmapSource image || image.PixelWidth==0 || image.PixelHeight==0)return Rect.Empty;
        return VideoOrientation.ImageBounds(new Size(Overlay.ActualWidth,Overlay.ActualHeight),image.PixelWidth,image.PixelHeight);
    }
    private Point? Position(MouseEventArgs e,bool clamp)
    {
        var bounds=Bounds();if(bounds.IsEmpty || bounds.Width==0 || bounds.Height==0)return null;
        var p=e.GetPosition(Overlay);if(!clamp && !bounds.Contains(p))return null;
        return VideoOrientation.ToSource(new Point(Math.Clamp((p.X-bounds.X)/bounds.Width,0,1),Math.Clamp((p.Y-bounds.Y)/bounds.Height,0,1)));
    }
    private void Draw()
    {
        if(Overlay is null)return;Overlay.Children.Clear();var bounds=Bounds();if(bounds.IsEmpty)return;
        var roi=selection ?? model?.Roi.ToArray();if(roi is null || roi.Length!=4)return;
        var displayed=VideoOrientation.ToDisplay(new Rect(roi[0],roi[1],roi[2],roi[3]));
        var box=new Rectangle{Width=displayed.Width*bounds.Width,Height=displayed.Height*bounds.Height,Stroke=selection is null?Brushes.MediumSpringGreen:Brushes.Gold,StrokeThickness=2,IsHitTestVisible=false};
        Canvas.SetLeft(box,bounds.X+displayed.X*bounds.Width);Canvas.SetTop(box,bounds.Y+displayed.Y*bounds.Height);Overlay.Children.Add(box);
    }
    private void Frame_SizeChanged(object sender,SizeChangedEventArgs e)=>Draw();
    private void Roi_Down(object sender,MouseButtonEventArgs e){if(model?.CanSetRoi!=true)return;start=Position(e,false);if(start is not null)Overlay.CaptureMouse();}
    private void Roi_Move(object sender,MouseEventArgs e)
    {
        if(start is not Point first || Position(e,true) is not Point p)return;
        selection=[Math.Min(first.X,p.X),Math.Min(first.Y,p.Y),Math.Abs(first.X-p.X),Math.Abs(first.Y-p.Y)];Draw();
    }
    private async void Roi_Up(object sender,MouseButtonEventArgs e)
    {
        var roi=selection;start=null;selection=null;Overlay.ReleaseMouseCapture();Draw();
        if(roi is {Length:4} && roi[2]>=.01 && roi[3]>=.01 && model is {} target)await target.SetRoiAsync(roi);
    }
    private void Roi_Cancel(object sender,MouseEventArgs e){start=null;selection=null;Draw();}
}
