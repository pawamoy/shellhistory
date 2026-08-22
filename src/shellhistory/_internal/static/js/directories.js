/* Where commands are run, and how deep. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.top)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#depth").empty();
      return;
    }
    SH.draw("container", data.top, { type: "bar", title: "Top directories", yTitle: "Commands" });
    SH.draw("depth", data.depth, {
      title: "How deep in the tree",
      xTitle: "Directory depth, counted in slashes",
      yTitle: "Commands"
    });
  }, page);
});
