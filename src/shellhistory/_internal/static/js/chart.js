/* The renderer for every chart that is a set of series over a set of buckets,
 * which is most of them. What it draws comes from the catalogue entry in
 * _app.py, so these charts need no script of their own.
 */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data)) {
      SH.note("container", "Nothing recorded for this selection.");
      return;
    }
    SH.annotate(data, "meanings", function (name, meaning) { return name + " – " + meaning; });
    SH.annotate(data, "seconds", function (name, seconds) { return name + "  (" + seconds + "s)"; });
    var spec = $.extend({}, page.spec);
    spec.subtitle = SH.summarize(data, page.spec.unit || "");
    SH.draw("container", data, spec);
  }, page);
});
