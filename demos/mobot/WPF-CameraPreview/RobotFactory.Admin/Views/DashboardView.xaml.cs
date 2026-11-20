using System.Windows.Controls;
using System.Windows;
using RobotFactory.Admin.ViewModels;
namespace RobotFactory.Admin.Views;
public partial class DashboardView : UserControl
{
    public DashboardView()
    {
        InitializeComponent();
        Loaded += async (_, _) =>
        {
            if (DataContext is DashboardViewModel model)
            {
                await model.StartAsync();
            }
        };
        Unloaded += (_, _) => (DataContext as DashboardViewModel)?.Stop();
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is DashboardViewModel model)
        {
            await model.StartAsync();
        }
    }

    private async void DropOrder_Click(object sender, RoutedEventArgs e)
    {
        if (DataContext is not DashboardViewModel model || !model.CanDropOrder || model.SelectedOrder is not { } order)
            return;
        var choice = MessageBox.Show(Window.GetWindow(this),
            $"{order.Company}\n{order.Id}\n\n선택한 주문을 취소하고 목록에서 제외할까요? 주문 기록은 DB에 보관됩니다.",
            "주문 드롭", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No);
        if (choice == MessageBoxResult.Yes) await model.DropOrderAsync(order);
    }
}
