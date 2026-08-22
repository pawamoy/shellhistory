/* How shells are used: how long they live, how much they hold, how many at once. */
$(function () {
  var page = window.CHART;
  SH.start(page.endpoint, function (data) {
    if (!data.shells) {
      SH.note("container", "Nothing recorded for this selection.");
      $("#size, #concurrency").empty();
      return;
    }
    SH.draw("container", data.length, {
      title: "How long a shell lives",
      xTitle: "From its first command to its last",
      yTitle: "Shells",
      subtitle: data.shells.toLocaleString() + " shells — " + SH.summarize(data.length, " min")
    });
    SH.draw("size", data.size, {
      title: "How many commands a shell holds",
      xTitle: "Commands in the shell",
      yTitle: "Shells",
      subtitle: SH.summarize(data.size, " commands")
    });
    SH.draw("concurrency", data.concurrency, {
      type: "line",
      title: "How many shells were alive at once",
      yTitle: "Shells at the same time",
      stacking: null
    });
  }, page);
});
