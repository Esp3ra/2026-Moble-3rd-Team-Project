using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;

class Detection
{
    public int ClassId;
    public float Score;
    public RectangleF Box;
}

static class InspectionEngine
{
    public const int MinimumFrames = 3;
    public const float SeenThreshold = 0.60f;
    public static readonly string[] Slots = { "head", "left", "right" };
    public static readonly string[] SlotNames = { "머리", "왼팔", "오른팔" };
    public static readonly string[][] Choices = {
        new[] { "H1", "H2" }, new[] { "L1", "L2", "L3" }, new[] { "R1", "R2", "R3" }
    };
    static readonly Dictionary<string, string> Codes = new Dictionary<string, string> {
        { "lime_dual_camera_head", "H1" }, { "yellow_infrared_monoeye_head", "H2" },
        { "left_blue_gripper_arm", "L1" }, { "right_blue_gripper_arm", "R1" },
        { "left_orange_welding_arm", "L2" }, { "right_orange_welding_arm", "R2" },
        { "left_red_fastening_arm", "L3" }, { "right_red_fastening_arm", "R3" }
    };

    public static string[] MapClasses(string[] names)
    {
        if (names.Length != Codes.Count || names.Distinct().Count() != Codes.Count || names.Any(name => !Codes.ContainsKey(name)))
            throw new ArgumentException("기존 8개 클래스 이름을 가진 model\\classes.txt가 필요합니다.");
        return names.Select(name => Codes[name]).ToArray();
    }

    public static void ValidateRegions(IList<RectangleF> regions)
    {
        if (regions.Count != Slots.Length)
            throw new ArgumentException("머리·로봇 기준 왼팔·오른팔 영역을 순서대로 지정하세요.");
        for (int i = 0; i < regions.Count; i++)
        {
            var area = regions[i];
            if (!(area.Left >= 0 && area.Top >= 0 && area.Right <= 1 && area.Bottom <= 1 &&
                  area.Width > 0 && area.Height > 0))
                throw new ArgumentException(SlotNames[i] + " 영역은 영상 안에 있어야 합니다.");
            for (int j = 0; j < i; j++)
                if (area.IntersectsWith(regions[j]))
                    throw new ArgumentException("검사 영역은 서로 겹칠 수 없습니다.");
        }
    }

    public static InspectionResult CaptureFailure(string[] expected, int frameCount)
    {
        var result = new InspectionResult(expected, frameCount) { verdict = "INDETERMINATE" };
        var hold = frameCount == 0 ? ObservationHold.NoFrame : ObservationHold.InsufficientFrames;
        result.reasons.Add(frameCount == 0 ? "NO_FRAME" : "INSUFFICIENT_FRAMES");
        foreach (string slot in Slots)
            result.observations.Add(slot, new PartObservation { observation = "UNKNOWN", Hold = hold });
        return result;
    }

    public static InspectionResult Evaluate(
        IList<InspectionFrame> frames, IList<RectangleF> regions, string[] expected,
        string[] classNames, bool visibilityConfirmed)
    {
        ValidateRegions(regions);
        if (expected.Length != Slots.Length || expected.Where((code, index) => !Choices[index].Contains(code)).Any())
            throw new ArgumentException("주문 부품 코드를 확인하세요.");
        var codes = MapClasses(classNames);
        if (frames.Any(frame => frame.Size.Width <= 0 || frame.Size.Height <= 0 ||
                                frame.Size != frames[0].Size || String.IsNullOrEmpty(frame.Fingerprint)))
            throw new ArgumentException("프레임 크기와 식별값을 확인하세요.");
        bool enoughDistinctFrames =
            frames.Select(frame => frame.Fingerprint).Distinct().Count() >= MinimumFrames;
        var result = new InspectionResult(expected, frames.Count);

        for (int i = 0; i < Slots.Length; i++)
        {
            var region = regions[i];
            string slot = Slots[i];
            var evidence = frames.Select(frame => frame.Hits.Where(hit =>
                region.Contains(
                    (hit.Box.Left + hit.Box.Width / 2) / frame.Size.Width,
                    (hit.Box.Top + hit.Box.Height / 2) / frame.Size.Height
                )).ToArray()).ToArray();
            var strong = evidence.Select(hits => hits.Where(hit => hit.Score >= SeenThreshold)
                .Select(hit => hit.ClassId).Distinct().ToArray()).ToArray();
            var observed = new PartObservation { observation = "UNKNOWN" };
            if (strong.Length > 0 && strong[0].Length == 1 &&
                strong.All(group => group.Length == 1 && group[0] == strong[0][0]))
            {
                int id = strong[0][0];
                observed.observation = "SEEN";
                observed.observed_id = codes[id];
                observed.confidence = Math.Round(evidence.Min(hits =>
                    hits.Where(hit => hit.ClassId == id).Max(hit => (double)hit.Score)), 4);
            }
            else if (frames.Count == 0)
            {
                observed.Hold = ObservationHold.NoFrame;
            }
            else if (evidence.All(hits => hits.Length == 0))
            {
                if (!enoughDistinctFrames) observed.Hold = ObservationHold.InsufficientFrames;
                else if (visibilityConfirmed) observed.observation = "MISSING";
                else observed.Hold = ObservationHold.VisibilityUnconfirmed;
            }
            else if (strong.All(group => group.Length == 0))
            {
                observed.Hold = ObservationHold.WeakSignal;
                observed.PeakScore = evidence.SelectMany(hits => hits).Max(hit => hit.Score);
            }
            else
            {
                observed.Hold = ObservationHold.UnstableSignal;
            }
            result.observations.Add(slot, observed);

            string reason = null;
            if (observed.observation == "MISSING") reason = "PART_MISSING";
            else if (observed.observation == "SEEN" && expected[i] != observed.observed_id)
                reason = "WRONG_PART";
            if (reason != null)
                result.defects.Add(new PartDefect {
                    slot = slot, reason = reason, expected_id = expected[i], observed_id = observed.observed_id,
                });
        }

        result.reasons.AddRange(result.defects.Select(defect => defect.reason).Distinct());
        if (frames.Count == 0) result.reasons.Add("NO_FRAME");
        else if (result.observations.Values.Any(value => value.observation == "UNKNOWN"))
            result.reasons.Add("LOW_CONFIDENCE");
        if (result.reasons.Contains("PART_MISSING")) result.verdict = "PART_MISSING";
        else if (result.reasons.Contains("WRONG_PART")) result.verdict = "MISMATCH";
        else result.verdict = result.reasons.Count == 0 ? "PASS" : "INDETERMINATE";
        return result;
    }
}

sealed class InspectionFrame
{
    public Size Size;
    public string Fingerprint;
    public List<Detection> Hits;
}

sealed class PartObservation
{
    public string observation { get; set; }
    public string observed_id { get; set; }
    public double? confidence { get; set; }
    internal ObservationHold Hold;
    internal float PeakScore;
}

enum ObservationHold
{
    None,
    NoFrame,
    InsufficientFrames,
    VisibilityUnconfirmed,
    WeakSignal,
    UnstableSignal,
}

sealed class PartDefect
{
    public string slot { get; set; }
    public string reason { get; set; }
    public string expected_id { get; set; }
    public string observed_id { get; set; }
}

sealed class InspectionResult
{
    public InspectionResult(string[] expectedCodes, int frameCount)
    {
        frame_count = frameCount;
        for (int i = 0; i < InspectionEngine.Slots.Length; i++)
            expected.Add(InspectionEngine.Slots[i], expectedCodes[i]);
    }

    public string verdict { get; set; }
    public int frame_count { get; set; }
    public readonly Dictionary<string, string> expected = new Dictionary<string, string>();
    public readonly Dictionary<string, PartObservation> observations = new Dictionary<string, PartObservation>();
    public readonly List<PartDefect> defects = new List<PartDefect>();
    public readonly List<string> reasons = new List<string>();

}
