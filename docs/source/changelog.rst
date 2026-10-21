Changelog
=========

- :release:`1.0.0 <21st October 2026>`
- :feature:`-` A fresh start on UPS's OAuth REST APIs: ``UPSClient`` signs in with an app's client ID
  and secret (client credentials) and keeps the token until it expires
- :feature:`-` ``get_tracking`` reads a package's status, delivery dates and activity from the
  Tracking API as a ``TrackResponse``
- :support:`-` Everything from the old ``darbiadev-ups`` package (the access licence number API) is gone
