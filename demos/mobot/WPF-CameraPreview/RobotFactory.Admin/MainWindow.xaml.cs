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
    private async void Logout_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is MainViewModel model) await model.LogoutAsync();
    }
}
