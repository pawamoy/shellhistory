/* How elaborate command lines get. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.ran) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#distribution, #lengths").empty();
      return;
    }
    SH.draw("container", data.overTime, {
      type: "line",
      title: "Operators per command, over time",
      yTitle: "Operators per command",
      valueDecimals: 3,
      subtitle: "On average " + data.parts.pipes.toFixed(3) + " pipes, " + data.parts.andOr.toFixed(3) +
        " && or ||, and " + data.parts.semicolons.toFixed(3) + " semicolons per command"
    });
    SH.draw("distribution", data.distribution, {
      title: "How many operators one command line strings together",
      xTitle: "Operators in the command line",
      yTitle: "Commands"
    });
    SH.draw("lengths", data.lengths, {
      type: "line",
      title: "Command length over time",
      yTitle: "Characters per command",
      valueDecimals: 1
    });
  }, page);
});
