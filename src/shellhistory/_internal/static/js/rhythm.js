/* Stretches of work, and the pauses that end them. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (SH.empty(data.length)) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#gaps").empty();
      return;
    }
    var names = Object.keys(data.length.summary || {});
    var subtitle = names.slice(0, 3).map(function (name) {
      var one = data.length.summary[name];
      return (names.length > 1 ? name + ": " : "") + one.stretches.toLocaleString() + " stretches over " +
        one.days.toLocaleString() + " days (" + one.perDay + " a day), longest " + one.longest + " min";
    }).join(" — ");

    SH.draw("container", data.length, {
      title: "How long a stretch of work lasts",
      xTitle: "Length of the stretch",
      yTitle: "Stretches",
      subtitle: subtitle
    });

    var table = $("<table>").addClass("table table-condensed sh-table");
    $("<thead>").append($("<tr>").append($("<th>").text("Away for"), $("<th>").text("From"), $("<th>").text("Until")))
      .appendTo(table);
    var body = $("<tbody>").appendTo(table);
    data.gaps.forEach(function (gap) {
      $("<tr>").append(
        $("<td>").text(gap.days + " days"),
        $("<td>").text(gap.from),
        $("<td>").text(gap.to)
      ).appendTo(body);
    });
    $("#gaps").empty().append($("<h3>").text("The longest silences")).append(table);
  }, page);
});
