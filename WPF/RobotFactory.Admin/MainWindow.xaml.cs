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
}
