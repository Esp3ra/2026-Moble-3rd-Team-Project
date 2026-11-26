using System.Windows.Controls;
using System.Windows;
using RobotFactory.Admin.ViewModels;
namespace RobotFactory.Admin.Views;
public partial class LoginView : UserControl
{
    public LoginView() => InitializeComponent();
    private async void Login_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is not LoginViewModel model) return;
        var password = PasswordInput.Password;
        PasswordInput.Clear();
        await model.LoginFromScreenAsync(password);
    }
}
