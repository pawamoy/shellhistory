/* How much of the history is run as somebody else. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.ran) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#count").empty();
      return;
    }
    SH.draw("container", data.share, {
      type: "line",
      title: "Share of commands run with sudo",
      yTitle: "% of commands",
      valueSuffix: " %",
      valueDecimals: 2,
      subtitle: data.raised.toLocaleString() + " of " + data.ran.toLocaleString() + " commands"
    });
    SH.draw("count", data.count, { type: "column", stacking: "auto", title: "How many", yTitle: "Commands" });
  }, page);
});
