using System.Windows;
using System.Windows.Controls;
using RobotFactory.Admin.ViewModels;
namespace RobotFactory.Admin.Views;
public partial class DashboardView:UserControl
{
 public DashboardView(){InitializeComponent();Loaded+=async(_,_)=>{if(DataContext is DashboardViewModel m)await m.StartAsync();};Unloaded+=(_,_)=>Model?.Stop();}
 private DashboardViewModel? Model=>DataContext as DashboardViewModel;
 private async void Reconcile_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.ReconcileAsync();}
 private async void Image_Click(object s,RoutedEventArgs e){if(Model is {} m && await m.InspectionImageAsync() is {} image)new Window{Title="보관 검사 사진",Width=900,Height=700,Owner=Window.GetWindow(this),Content=new Image{Source=image,Stretch=System.Windows.Media.Stretch.Uniform}}.Show();}
 private async void DeleteImage_Click(object s,RoutedEventArgs e){if(Model is {} m && MessageBox.Show(Window.GetWindow(this),"선택한 보관 검사 사진을 삭제할까요?","사진 삭제",MessageBoxButton.YesNo)==MessageBoxResult.Yes)await m.DeleteImageAsync();}
 private async void Accept_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.AcceptAsync();}
 private async void Cancel_Click(object s,RoutedEventArgs e){if(Model is {} m && MessageBox.Show(Window.GetWindow(this),"선택 주문을 취소하고 재고를 복구할까요?","주문 취소",MessageBoxButton.YesNo)==MessageBoxResult.Yes)await m.CancelAsync();}
 private async void Start_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.PlantActionAsync("START");}
 private async void Pause_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.PlantActionAsync("PAUSE");}
 private async void Resume_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.PlantActionAsync("RESUME");}
 private async void ReadQr_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.RetryQrAsync("READ_QR");}
 private async void SaveQr_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.RetryQrAsync("SAVE_BINDING");}
 private async void More_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.LoadMoreAsync();}
 private async void Restock_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.RestockAsync();}
 private async void CreateAccount_Click(object s,RoutedEventArgs e){var password=NewPassword.Password;NewPassword.Clear();if(Model is {} m)await m.CreateAccountAsync(password);}
 private async void ChangeRole_Click(object s,RoutedEventArgs e){if(Model is {} m)await m.ChangeRoleAsync();}
}
