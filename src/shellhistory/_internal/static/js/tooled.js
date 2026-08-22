/* The two charts that need a program picked first: subcommands, and options. */
$(function () {
  var page = window.CHART;

  function picker(data) {
    if ($("#sh-tool").length) { return; }
    var select = $("<select>").addClass("form-control input-sm").attr("id", "sh-tool");
    if (page.spec.any) {
      select.append($("<option>").attr("value", "").text("every command").prop("selected", !data.tool));
    }
    (data.tools || []).forEach(function (name) {
      select.append($("<option>").attr("value", name).text(name).prop("selected", data.tool === name));
    });
    select.on("change", function () { SH.set("tool", this.value); });
    $("#sh-controls").prepend(
      $("<div>").addClass("sh-row").append($("<label>").text("Show " + page.spec.picker + " ").append(select))
    );
  }

  SH.start(page.endpoint, function (data) {
    picker(data);
    if (SH.empty(data)) {
      SH.note("container", "Nothing recorded for " + (data.tool || "this selection") + ".");
      return;
    }
    SH.draw("container", data, $.extend({}, page.spec, {
      title: page.spec.title + (data.tool ? " of " + data.tool : "")
    }));
  }, page);
});
