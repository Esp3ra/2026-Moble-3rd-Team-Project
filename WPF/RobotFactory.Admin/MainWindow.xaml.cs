using System.Windows;
using RobotFactory.Admin.ViewModels;

namespace RobotFactory.Admin;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        DataContext = new MainViewModel();
    }

    protected override void OnClosed(EventArgs e)
    {
        (DataContext as MainViewModel)?.Dispose();
        base.OnClosed(e);
    }

    private async void Logout_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel model) await model.LogoutAsync();
    }
}
