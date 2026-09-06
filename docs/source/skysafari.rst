===============
SkySafari
===============


Network Setup
-------------

First, make sure your device is on the same network as the PiFinder.  See :doc:`connectivity` for changing WiFi modes and finding the PiFinder's IP address.

App Setup
---------

Connecting requires SkySafari Plus or Pro.  Start by setting up a telescope profile from the Telescope section of the settings page:


.. image:: images/SkySafari/IMG_4792.jpeg
   :alt: Setup


Click 'Presets', then use the + button at the bottom right to add a new profile.


.. image:: images/SkySafari/IMG_4793.jpeg
   :alt: Type


Select 'Other' as the telescope type.


.. image:: images/SkySafari/IMG_4794.jpeg
   :alt: Setup


Choose 'Alt-Az. GoTo' as the mount type, even without a GoTo scope — GoTo lets you send objects from SkySafari to the PiFinder observing list.


.. image:: images/SkySafari/IMG_4796.jpeg
   :alt: Setup


Select 'Meade LX200 Classic' for the scope type and click 'Next'.


.. image:: images/SkySafari/IMG_4797.jpeg
   :alt: Setup


Use ``pifinder.local`` for the IP address; if that doesn't work, check the Status screen for the numeric IP.  Set the port to 4030, the SkySafari default.

Click 'Next' to continue.


.. image:: images/SkySafari/IMG_4798.jpeg
   :alt: Setup


The default Readout rate and Timeout are fine.  Name your profile and click 'Save Preset' to save it and make it active.

Now select the Telescope icon on the main SkySafari screen and click connect to start receiving position updates.  Until the first solve completes, the PiFinder sends a default location (0 degrees RA/DEC).

Using SkySafari
---------------

Once connected, SkySafari and the PiFinder work together in two main ways:

* **Follow your scope on the star chart.**  As you move the telescope, the PiFinder reports
  its solved position and SkySafari marks it on its chart — a large, zoomable view of where
  you are pointed.  This is especially handy near the zenith, where the PiFinder's own
  Push-To numbers become twitchy.
* **Send targets to the PiFinder.**  Pick an object in SkySafari and send it to the
  PiFinder's observing list, then use Push-To guidance to find it — a comfortable
  alternative to entering objects with the keypad.

A few things are worth knowing about the connection today:

* By default, SkySafari reads PiFinder position and sends targets into the PiFinder
  observing flow.
* On MF PiFinder builds with INDI mount control configured, SkySafari GoTo, Sync/Align,
  and guide button commands can optionally be forwarded to the INDI mount.  Enable that
  only after the INDI mount setup works from the PiFinder INDI page.
* Only **one** device can connect to the PiFinder at a time.  To connect a different phone
  or tablet, disconnect the first one.
* When guide forwarding is enabled, SkySafari direction buttons move while held and stop
  when SkySafari sends the release/stop command.  A safety timeout stops motion if a stop
  command is lost.
* SkySafari 5 Plus, 6, and 7 all work; version 7 is the most reliable.

.. note::
   If the PiFinder drops into power-save mode it stops sending position updates, so
   SkySafari appears to freeze.  When you are relying on SkySafari, lengthen or turn off
   the sleep timer (see :ref:`quick_start:adjusting brightness`).

Stellarium Mobile Plus (MF fork)
-------------------------------

Connect to the same network as the PiFinder and configure an LX200-compatible TCP
telescope connection to ``pifinder.local`` (or its numeric IP), port **4030**. Only one
planetarium connection is served at a time.

Stellarium can read the pointing and send a target. In this fork, receiving target
coordinates does not itself start motion: the subsequent LX200 GoTo command does.
This preserves the existing SkySafari Align and INDI routing. With mount control
enabled, a GoTo can move the mount according to the configured MF GoTo method;
with it disabled, it only adds the object to PiFinder's recent/Push-To view.
The existing SkySafari-named GoTo/Align settings also govern this connection.

The client's site setters are echoed only within that connection. Its site and
clock never replace the PiFinder GPS/manual location, system time or mount site.
After disconnect, echoed site values are discarded. Target coordinates survive
successive connections from the same client IP, since SkySafari can send each
coordinate and GoTo/Align command on a separate connection. A different client IP
or a communication gap of 60 seconds clears the previous target.
The existing MF coordinate convention is unchanged; no additional epoch conversion
is introduced by this port.

Protocol tests cover both apps' framing and replies, but a physical Mobile Plus
session and mount motion still need field verification before relying on this port.

Troubleshooting
---------------

**SkySafari won't connect, or the connection keeps dropping.**
The usual cause is your phone or tablet quietly leaving the ``PiFinderAP`` network.  Because
it has no internet access, many devices switch back to cellular or a home network in the
background, breaking the link.  Re-select ``PiFinderAP`` in your WiFi settings, and turn off
any "smart network switching" or "auto-switch to mobile data" option.

**``pifinder.local`` doesn't resolve.**
Some phones and networks can't reliably look up the ``.local`` name.  Use the PiFinder's
numeric IP instead — you'll find it on the Status screen.  In Access Point mode that address
is ``10.10.10.1``.

**It connects, but the position never updates.**
Until the first plate solve completes, the PiFinder reports 0°/0°, so give it a moment with
the camera focused on the sky.  If the position was updating and then froze, the PiFinder has
most likely entered power-save mode — see the note above.

**The connection is intermittent at a star party.**
Two nearby PiFinders using the same network name (SSID) can interfere with each other.  Give
each one a distinct network name to avoid this.
