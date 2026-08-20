$(document).ready(function () {
  $('#import-close').click(function (e) {
    $(this).parent().fadeOut(500);
  });
  // The shell writes straight to the database now, so this is only for loading
  // an archived history file in the old colon-delimited text format.
  $("#import-button").click(function () {
    $.getJSON('/import_legacy', function (data) {
      $('#import-message').text(data.message);
      $('#import-alert')
        .removeClass()
        .addClass(data.class)
        .show();
    });
  });
});
