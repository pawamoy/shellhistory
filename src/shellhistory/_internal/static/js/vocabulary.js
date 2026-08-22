/* How wide a vocabulary of programs is in use, and how it grows. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.distinct)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#new").empty();
      return;
    }
    SH.draw("container", data.distinct, {
      type: "line",
      title: "Different programs used",
      yTitle: "Distinct programs",
      subtitle: data.total.toLocaleString() + " distinct programs in all"
    });
    SH.draw("new", data.new, {
      type: "column",
      stacking: "auto",
      title: "Programs used for the first time",
      yTitle: "New programs"
    });
  }, page);
});
