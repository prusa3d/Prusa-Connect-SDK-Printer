Prusa Connect SDK for Printer
=============================

.. code:: python

    from prusa.connect.printer import Printer
    from time import sleep

    printer = Printer(fingerprint="", token="")

    while True:
        # send telemetry per second
        printer.send_telemetry(state=Printer.State.READY)
        sleep(1)
