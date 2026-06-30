[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xml = @'
<toast duration="long">
    <visual>
        <binding template="ToastGeneric">
            <text>&#x23F0; &#x4F11;&#x606F;&#x63D0;&#x9192;</text>
            <text>&#x8FDE;&#x7EED;&#x5DE5;&#x4F5C;&#x592A;&#x4E45;&#x5566;&#xFF01;&#x5C4F;&#x5E55;&#x5C06;&#x5728; 5 &#x79D2;&#x540E;&#x5173;&#x95ED;&#xFF0C;&#x4F11;&#x606F; 5 &#x5206;&#x949F;&#x3002;&#x79FB;&#x52A8;&#x9F20;&#x6807;&#x5373;&#x53EF;&#x5524;&#x9192;&#x3002;</text>
        </binding>
    </visual>
    <audio src="ms-winsoundevent:Notification.Reminder"/>
</toast>
'@

$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDoc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDoc)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("StickyWallpaper")
$notifier.Show($toast)
