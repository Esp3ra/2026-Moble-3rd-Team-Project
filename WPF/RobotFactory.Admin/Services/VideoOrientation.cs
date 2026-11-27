using System.Windows;

namespace RobotFactory.Admin.Services;

/// <summary>Display-only 90 degree clockwise mapping; server ROI stays in source coordinates.</summary>
public static class VideoOrientation
{
    public static Rect ImageBounds(Size viewport,int pixelWidth,int pixelHeight)
    {
        if(pixelWidth<=0 || pixelHeight<=0 || viewport.Width<=0 || viewport.Height<=0)return Rect.Empty;
        var scale=Math.Min(viewport.Width/pixelHeight,viewport.Height/pixelWidth);
        var width=pixelHeight*scale;var height=pixelWidth*scale;
        return new Rect((viewport.Width-width)/2,(viewport.Height-height)/2,width,height);
    }
    public static Point ToSource(Point display)=>new(display.Y,1-display.X);
    public static Rect ToDisplay(Rect source)=>new(1-source.Y-source.Height,source.X,source.Height,source.Width);
}
