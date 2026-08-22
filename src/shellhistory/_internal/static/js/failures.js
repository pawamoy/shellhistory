/* How often commands fail, over time and by command. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.ran) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#commands").empty();
      return;
    }
    SH.draw("container", data.overTime, {
      type: "line",
      title: "Failure rate over time",
      yTitle: "% of commands that failed",
      valueSuffix: " %",
      valueDecimals: 2,
      subtitle: data.broke.toLocaleString() + " of " + data.ran.toLocaleString() +
        " commands failed, " + data.rate + " % overall"
    });
    SH.draw("commands", data.byCommand, {
      type: "bar",
      title: "The commands that fight back",
      yTitle: "% of runs that failed",
      valueSuffix: " %",
      valueDecimals: 2,
      subtitle: "Ranked by failure rate, among commands run at least " + data.minimum + " times"
    });
  }, page);
});
