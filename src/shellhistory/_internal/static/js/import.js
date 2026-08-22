/* The one thing on every page that has nothing to do with charts: loading an
 * archived history file written by a version that did not write to the database.
 */
$(function () {
  $("#import-close").click(function () {
    $(this).parent().fadeOut(500);
  });
  $("#import-button").click(function (event) {
    event.preventDefault();
    $.getJSON("/import_legacy", function (data) {
      $("#import-message").text(data.message);
      $("#import-alert").removeClass().addClass(data.class).show();
    });
  });
});
