using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;

partial class CameraWindow
{
    readonly Button setRegions = new Button { Text = "검사 영역 지정", Enabled = false, AutoSize = true };
    readonly Button inspect = new Button { Text = "검사", Enabled = false, AutoSize = true };
    readonly Button copyJson = new Button { Text = "결과 JSON 복사", Enabled = false, AutoSize = true };
    readonly CheckBox confirmed = new CheckBox { Text = "정위치·가림 없음 확인", AutoSize = true };
    readonly Label inspectionStatus = new Label { Dock = DockStyle.Fill, AutoEllipsis = true };
    readonly ComboBox[] partChoices = new ComboBox[3];
    readonly List<RectangleF> regions = new List<RectangleF>();
    PointF? dragStart;
    PointF dragEnd;
    bool selectingRegions;
    InspectionRequest request;
    InspectionResult lastResult;

    sealed class InspectionRequest
    {
        public RectangleF[] Regions;
        public string[] Expected;
        public bool Confirmed;
        public DateTime Started = DateTime.UtcNow;
        public DateTime NextSample = DateTime.UtcNow;
        public readonly List<InspectionFrame> Frames = new List<InspectionFrame>();
    }

    void AddInspectionControls(FlowLayoutPanel cameraBar)
    {
        Controls.Remove(cameraBar);
        var header = new TableLayoutPanel { Dock = DockStyle.Top, ColumnCount = 1, RowCount = 3, Height = 108 };
        header.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
        header.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        header.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        cameraBar.Dock = DockStyle.Fill;
        header.Controls.Add(cameraBar, 0, 0);
        var row = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false };
        for (int i = 0; i < partChoices.Length; i++)
        {
            var box = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 64 };
            box.Items.AddRange(InspectionEngine.Choices[i]);
            box.SelectedIndexChanged += delegate {
                if (request != null || lastResult != null)
                {
                    request = null;
                    lastResult = null;
                    inspectionStatus.Text = "주문 부품이 변경됐습니다. 다시 검사하세요.";
                }
                confirmed.Checked = false;
                UpdateInspectionControls();
            };
            partChoices[i] = box;
            row.Controls.Add(new Label { Text = InspectionEngine.SlotNames[i], AutoSize = true, Padding = new Padding(2, 7, 0, 0) });
            row.Controls.Add(box);
        }
        row.Controls.AddRange(new Control[] { setRegions, confirmed, inspect, copyJson });
        header.Controls.Add(row, 0, 1);
        header.Controls.Add(inspectionStatus, 0, 2);
        Controls.Add(header);
        inspectionStatus.Padding = new Padding(7, 4, 0, 0);
        inspectionStatus.Text = "카메라를 연 뒤 검사 영역과 주문 부품을 선택하세요.";

        setRegions.Click += delegate {
            ResetRegions();
            selectingRegions = true;
            inspectionStatus.Text = "영상에서 머리 부분을 드래그하세요.";
        };
        inspect.Click += delegate {
            lastResult = null;
            request = new InspectionRequest {
                Regions = regions.ToArray(),
                Expected = partChoices.Select(box => (string)box.SelectedItem).ToArray(),
                Confirmed = confirmed.Checked,
            };
            confirmed.Checked = false;
            inspectionStatus.Text = "검사 중…";
            UpdateInspectionControls();
        };
        copyJson.Click += delegate {
            if (lastResult != null) Clipboard.SetText(Program.ResultJson(lastResult));
        };
        preview.MouseDown += delegate(object sender, MouseEventArgs e) {
            if (selectingRegions && e.Button == MouseButtons.Left && VideoBounds().Contains(e.Location))
            {
                dragStart = dragEnd = NormalizedPoint(e.Location);
                preview.Capture = true;
            }
        };
        preview.MouseMove += delegate(object sender, MouseEventArgs e) {
            if (!dragStart.HasValue) return;
            dragEnd = NormalizedPoint(e.Location);
            preview.Invalidate();
        };
        preview.MouseUp += delegate(object sender, MouseEventArgs e) {
            if (!dragStart.HasValue || e.Button != MouseButtons.Left) return;
            var area = RectangleBetween(dragStart.Value, NormalizedPoint(e.Location));
            dragStart = null;
            preview.Capture = false;
            if (area.Width < .02f || area.Height < .02f || regions.Any(region => region.IntersectsWith(area)))
            {
                inspectionStatus.Text = "다른 영역과 겹치지 않게 " + InspectionEngine.SlotNames[regions.Count] + " 부분을 다시 드래그하세요.";
            }
            else
            {
                regions.Add(area);
                selectingRegions = regions.Count < InspectionEngine.Slots.Length;
                inspectionStatus.Text = selectingRegions
                    ? "로봇 기준 " + InspectionEngine.SlotNames[regions.Count] + " 부분을 드래그하세요."
                    : "영역 지정 완료. 주문 부품을 선택하고 검사 버튼을 누르세요.";
            }
            preview.Invalidate();
            UpdateInspectionControls();
        };
        preview.Paint += delegate(object sender, PaintEventArgs e) {
            var bounds = VideoBounds();
            if (bounds.IsEmpty) return;
            using (var pen = new Pen(Color.Gold, 2))
            {
                for (int i = 0; i < regions.Count; i++)
                {
                    var box = ToPixels(bounds, regions[i]);
                    e.Graphics.DrawRectangle(pen, box.X, box.Y, box.Width, box.Height);
                    e.Graphics.DrawString(InspectionEngine.SlotNames[i], Font, Brushes.Gold, box.Location);
                }
                if (dragStart.HasValue)
                {
                    var box = ToPixels(bounds, RectangleBetween(dragStart.Value, dragEnd));
                    e.Graphics.DrawRectangle(pen, box.X, box.Y, box.Width, box.Height);
                }
            }
        };
    }

    RectangleF VideoBounds()
    {
        if (preview.Image == null || preview.ClientSize.Width == 0 || preview.ClientSize.Height == 0)
            return RectangleF.Empty;
        var scale = Math.Min((float)preview.ClientSize.Width / preview.Image.Width,
                             (float)preview.ClientSize.Height / preview.Image.Height);
        float width = preview.Image.Width * scale, height = preview.Image.Height * scale;
        return new RectangleF((preview.ClientSize.Width - width) / 2, (preview.ClientSize.Height - height) / 2, width, height);
    }

    PointF NormalizedPoint(Point point)
    {
        var bounds = VideoBounds();
        return new PointF(Math.Max(0, Math.Min(1, (point.X - bounds.X) / bounds.Width)),
                          Math.Max(0, Math.Min(1, (point.Y - bounds.Y) / bounds.Height)));
    }

    static RectangleF RectangleBetween(PointF a, PointF b)
    {
        return RectangleF.FromLTRB(Math.Min(a.X, b.X), Math.Min(a.Y, b.Y), Math.Max(a.X, b.X), Math.Max(a.Y, b.Y));
    }

    static RectangleF ToPixels(RectangleF view, RectangleF rect)
    {
        return new RectangleF(view.X + rect.X * view.Width, view.Y + rect.Y * view.Height,
                              rect.Width * view.Width, rect.Height * view.Height);
    }

    void ResetRegions()
    {
        request = null;
        lastResult = null;
        dragStart = null;
        selectingRegions = false;
        confirmed.Checked = false;
        regions.Clear();
        preview.Invalidate();
        UpdateInspectionControls();
    }

    void UpdateInspectionControls()
    {
        bool busy = request != null;
        setRegions.Enabled = running && preview.Image != null && !busy;
        inspect.Enabled = setRegions.Enabled && regions.Count == 3 && partChoices.All(box => box.SelectedIndex >= 0);
        rotate.Enabled = !busy;
        confirmed.Enabled = !busy;
        foreach (var box in partChoices) box.Enabled = !busy;
        copyJson.Enabled = lastResult != null;
    }

    bool ShouldSample()
    {
        if (request == null) return false;
        if ((DateTime.UtcNow - request.Started).TotalSeconds > 5)
        {
            FinishIncompleteCapture();
            return false;
        }
        return DateTime.UtcNow >= request.NextSample;
    }

    void RecordSample(Bitmap image, List<Detection> hits, string fingerprint, string[] names)
    {
        if (request.Frames.Any(frame => frame.Fingerprint == fingerprint)) return;
        request.Frames.Add(new InspectionFrame { Size = image.Size, Fingerprint = fingerprint, Hits = hits });
        request.NextSample = DateTime.UtcNow.AddMilliseconds(150);
        if (request.Frames.Count < InspectionEngine.MinimumFrames)
        {
            inspectionStatus.Text = "검사 중 " + request.Frames.Count + "/3";
            return;
        }
        var current = request;
        lastResult = InspectionEngine.Evaluate(current.Frames, current.Regions, current.Expected, names, current.Confirmed);
        string[] uncheckedSlots = InspectionEngine.Slots
            .Where(slot => lastResult.observations[slot].Hold == ObservationHold.VisibilityUnconfirmed)
            .Select(SlotName).ToArray();
        if (uncheckedSlots.Length > 0)
        {
            string emptySlots = String.Join(", ", uncheckedSlots);
            var response = MessageBox.Show(this,
                emptySlots + " 자리에서 3장 모두 부품 후보가 없었습니다.\n\n" +
                "촬영된 영상에서 몸통이 기준 위치에 있고 해당 자리가 가려지지 않았나요?",
                "누락 후보 확인", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
            if (response == DialogResult.Yes)
                lastResult = InspectionEngine.Evaluate(current.Frames, current.Regions, current.Expected, names, true);
        }
        FinishInspection(lastResult);
    }

    void FinishIncompleteCapture()
    {
        FinishInspection(InspectionEngine.CaptureFailure(request.Expected, request.Frames.Count));
    }

    void FinishInspection(InspectionResult result)
    {
        request = null;
        lastResult = result;
        inspectionStatus.Text = DescribeInspection(result);
        UpdateInspectionControls();
    }

    static string SlotName(string slot)
    {
        return InspectionEngine.SlotNames[Array.IndexOf(InspectionEngine.Slots, slot)];
    }

    internal static string DescribeInspection(InspectionResult result)
    {
        if (result.verdict == "PASS") return "정상 · 머리와 양팔 일치";
        if (result.reasons.Contains("NO_FRAME")) return "판정 보류 · 검사용 프레임이 없습니다.";
        if (result.reasons.Contains("INSUFFICIENT_FRAMES"))
            return "판정 보류 · 서로 다른 프레임 3장을 얻지 못했습니다.";
        var messages = result.defects.Select(defect => SlotName(defect.slot) +
            (defect.reason == "PART_MISSING" ? " 누락" : " 오장착 (" + defect.expected_id + " → " + defect.observed_id + ")")
        ).ToList();
        foreach (string slot in InspectionEngine.Slots)
        {
            var observed = result.observations[slot];
            string reason;
            switch (observed.Hold)
            {
                case ObservationHold.VisibilityUnconfirmed:
                    reason = "누락 후보 · 정위치와 가림 확인 필요";
                    break;
                case ObservationHold.WeakSignal:
                    reason = String.Format("판정 보류 · 낮은 점수 {0:F2}", observed.PeakScore);
                    break;
                case ObservationHold.UnstableSignal:
                    reason = "판정 보류 · 프레임별 검출 불일치";
                    break;
                case ObservationHold.InsufficientFrames:
                    reason = "판정 보류 · 서로 다른 프레임 부족";
                    break;
                default:
                    continue;
            }
            messages.Add(SlotName(slot) + " " + reason);
        }
        return String.Join(" / ", messages);
    }
}
