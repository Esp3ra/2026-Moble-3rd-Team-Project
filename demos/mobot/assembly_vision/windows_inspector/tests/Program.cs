using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;

static class Program
{
    static void Main()
    {
        var regions = new[] {
            new RectangleF(.4f, 0, .2f, .25f),
            new RectangleF(.7f, .3f, .25f, .4f),
            new RectangleF(.05f, .3f, .25f, .4f),
        };
        var expected = new[] { "H1", "L1", "R1" };
        var classes = File.ReadAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "classes.txt"));
        var empty = new[] { Frame("a", null), Frame("b", null), Frame("c", null) };
        var matching = new[] { Frame("a", .92f), Frame("b", .93f), Frame("c", .94f) };
        var weak = new[] { Frame("a", .32f), Frame("b", .41f), Frame("c", null) };
        var unstable = new[] { Frame("a", .91f), Frame("b", null), Frame("c", .93f) };

        Expect(InspectionEngine.Evaluate(matching, regions, expected, classes, true).verdict == "PASS", "matching parts");
        var wrong = InspectionEngine.Evaluate(matching, regions, new[] { "H1", "L2", "R1" }, classes, true);
        Expect(wrong.verdict == "MISMATCH" && wrong.defects.Count == 1 &&
            wrong.defects[0].reason == "WRONG_PART" && wrong.defects[0].observed_id == "L1", "wrong left part");
        var waiting = InspectionEngine.Evaluate(empty, regions, expected, classes, false);
        Expect(waiting.verdict == "INDETERMINATE" &&
            waiting.observations["left"].Hold == ObservationHold.VisibilityUnconfirmed, "empty region without confirmation");
        var missing = InspectionEngine.Evaluate(empty, regions, expected, classes, true);
        Expect(missing.verdict == "PART_MISSING" && missing.defects.Count == 1 &&
            missing.defects[0].slot == "left", "confirmed left part missing");
        var repeated = new[] { empty[0], empty[0], empty[0] };
        Expect(InspectionEngine.Evaluate(repeated, regions, expected, classes, true)
            .observations["left"].Hold == ObservationHold.InsufficientFrames, "repeated frames");
        Expect(InspectionEngine.Evaluate(weak, regions, expected, classes, true)
            .observations["left"].Hold == ObservationHold.WeakSignal, "weak detection");
        Expect(InspectionEngine.Evaluate(unstable, regions, expected, classes, true)
            .observations["left"].Hold == ObservationHold.UnstableSignal, "unstable detection");
        var combined = InspectionEngine.Evaluate(empty, regions, new[] { "H2", "L1", "R1" }, classes, true);
        Expect(combined.verdict == "PART_MISSING" && combined.defects.Count == 2, "both defect types reported");
        var noFrame = InspectionEngine.CaptureFailure(expected, 0);
        Expect(noFrame.verdict == "INDETERMINATE" && noFrame.reasons.Count == 1 &&
            noFrame.reasons[0] == "NO_FRAME" && noFrame.frame_count == 0, "capture without frames");
        var incomplete = InspectionEngine.CaptureFailure(expected, 2);
        Expect(incomplete.verdict == "INDETERMINATE" && incomplete.reasons.Count == 1 &&
            incomplete.reasons[0] == "INSUFFICIENT_FRAMES" && incomplete.frame_count == 2, "capture with missing frames");
        Expect(incomplete.expected["left"] == "L1" && incomplete.defects.Count == 0 &&
            incomplete.observations["left"].observation == "UNKNOWN", "incomplete capture retains order and unknown observation");
        Console.WriteLine("Inspection smoke tests passed.");
    }

    static InspectionFrame Frame(string id, float? leftScore)
    {
        var hits = new List<Detection> {
            new Detection { ClassId = 0, Score = .94f, Box = new RectangleF(45, 5, 10, 10) },
            new Detection { ClassId = 3, Score = .94f, Box = new RectangleF(15, 45, 10, 10) },
        };
        if (leftScore.HasValue)
            hits.Add(new Detection { ClassId = 2, Score = leftScore.Value, Box = new RectangleF(75, 45, 10, 10) });
        return new InspectionFrame { Size = new Size(100, 100), Fingerprint = id, Hits = hits };
    }

    static void Expect(bool condition, string description)
    {
        if (!condition) throw new InvalidOperationException(description);
    }
}
