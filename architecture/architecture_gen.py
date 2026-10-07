"""Generate the Aussie EcoGuard multi-cloud architecture as a draw.io file."""
from xml.sax.saxutils import escape

cells = []

AWS_ICON = ("sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor={c};"
            "strokeColor=#ffffff;dashed=0;verticalLabelPosition={vp};verticalAlign={va};align=center;"
            "html=1;fontSize=12;fontStyle=0;aspect=fixed;pointerEvents=1;"
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};")
AWS_RES = ("sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor={c};"
           "strokeColor=none;dashed=0;verticalLabelPosition={vp};verticalAlign={va};align=center;"
           "html=1;fontSize=12;fontStyle=0;aspect=fixed;pointerEvents=1;shape=mxgraph.aws4.{icon};")
GCP_ICON = ("sketch=0;html=1;fillColor=#5184F3;strokeColor=none;verticalLabelPosition={vp};"
            "verticalAlign={va};labelPosition=center;align=center;spacingTop=-6;fontSize=12;"
            "fontColor=#232F3E;shape=mxgraph.gcp2.hexIcon;prIcon={icon};")

COMPUTE, STORAGE, DB, SEC, APPINT, ML = "#ED7100", "#7AA116", "#C925D1", "#DD344C", "#E7157B", "#01A88D"


def pos(label_top):
    return ("top", "bottom") if label_top else ("bottom", "top")


def vertex(id_, label, style, x, y, w, h):
    cells.append(f'<mxCell id="{id_}" value="{escape(label, {chr(34): "&quot;"})}" style="{style}" '
                 f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def aws(id_, label, icon, color, x, y, size=60, top=False):
    vp, va = pos(top)
    vertex(id_, label, AWS_ICON.format(c=color, icon=icon, vp=vp, va=va), x, y, size, size)


def res(id_, label, icon, color, x, y, size=48, top=False):
    vp, va = pos(top)
    vertex(id_, label, AWS_RES.format(c=color, icon=icon, vp=vp, va=va), x, y, size, size)


def gcp(id_, label, icon, x, y, top=False):
    vp, va = pos(top)
    vertex(id_, label, GCP_ICON.format(icon=icon, vp=vp, va=va), x, y, 66, 58)


def edge(id_, src, tgt, label="", dashed=False, points=(), extra="", both=False):
    style = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;"
             "strokeColor=#545B64;strokeWidth=1.5;fontSize=13;fontStyle=1;fontColor=#232F3E;"
             "labelBackgroundColor=#ffffff;")
    if dashed:
        style += "dashed=1;dashPattern=6 4;"
    if both:
        style += "startArrow=block;startFill=1;"
    style += extra
    pts = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    arr = f'<Array as="points">{pts}</Array>' if points else ""
    cells.append(f'<mxCell id="{id_}" value="{label}" style="{style}" edge="1" parent="1" '
                 f'source="{src}" target="{tgt}"><mxGeometry relative="1" as="geometry">{arr}'
                 f'</mxGeometry></mxCell>')


# ---------------- Groups ----------------
vertex("awsCloud", "AWS Cloud (us-east-1)",
       "points=[];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=14;fontStyle=1;"
       "container=0;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;"
       "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;verticalAlign=top;"
       "align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;",
       170, 20, 1200, 1010)
vertex("lambdaGrp", "API Lambda functions",
       "rounded=1;arcSize=3;fillColor=none;strokeColor=#ED7100;dashed=1;verticalAlign=top;align=left;"
       "spacingLeft=8;fontStyle=1;fontColor=#ED7100;fontSize=12;html=1;",
       400, 45, 240, 565)
vertex("gcpCloud", "Google Cloud (australia-southeast1)",
       "rounded=1;arcSize=3;fillColor=#F1F8FE;strokeColor=#4285F4;strokeWidth=1.5;verticalAlign=top;"
       "align=left;spacingLeft=40;spacingTop=4;fontStyle=1;fontColor=#1A73E8;fontSize=14;html=1;",
       1420, 560, 320, 420)
vertex("gcpLogo", "", "sketch=0;html=1;aspect=fixed;strokeColor=none;shadow=0;fillColor=#4285F4;"
       "shape=mxgraph.gcp2.google_cloud_platform;", 1428, 566, 28, 22)

# ---------------- Actors ----------------
vertex("user", "User<br>(web browser)", AWS_RES.format(c="#232F3D", icon="user", vp="middle", va="middle") + "labelPosition=left;align=right;", 40, 470, 60, 60)
res("email", "Subscriber<br>email inbox", "email_notification", APPINT, 1490, 306, 48)

# ---------------- AWS front tier ----------------
aws("s3web", "Amazon S3<br>static website (UI)", "s3", STORAGE, 240, 70)
aws("cognito", "Amazon Cognito<br>User Pool + Hosted UI", "cognito", SEC, 240, 250)
aws("apigw", "Amazon API Gateway<br>REST + Cognito authorizer", "api_gateway", APPINT, 240, 430)

# ---------------- API Lambdas ----------------
res("lUpload", "upload-url<br>(SHA-256 dedup, presigned PUT)", "lambda_function", COMPUTE, 496, 80)
res("lSearch", "search<br>(tags / species / lost pet /<br>thumbnail &rarr; original)", "lambda_function", COMPUTE, 496, 185)
res("lTags", "edit-tags<br>(bulk add / remove)", "lambda_function", COMPUTE, 496, 305)
res("lDelete", "delete-files", "lambda_function", COMPUTE, 496, 410)
res("lChat", "chat<br>(Lex proxy)", "lambda_function", COMPUTE, 496, 505)
aws("lex", "Amazon Lex V2<br>chatbot (intents / slots)", "lex", ML, 720, 499)

# ---------------- Data + notification ----------------
aws("ddb", "Amazon DynamoDB<br>Files table + TagIndex table", "dynamodb", DB, 900, 300)
res("lNotify", "notify", "lambda_function", COMPUTE, 1086, 306)
aws("sns", "Amazon SNS<br>topic (tag filter policy)", "sns", APPINT, 1230, 300)

# ---------------- Upload pipeline ----------------
aws("s3media", "Amazon S3 media bucket<br>uploads/ &middot; thumbnails/", "s3", STORAGE, 490, 760)
res("lTag", "tagging<br>(video: 1 frame / 10 s)", "lambda_function", COMPUTE, 1006, 700)
res("lThumb", "thumbnail<br>(OpenCV resize + compress)", "lambda_function", COMPUTE, 760, 860)
aws("secrets", "AWS Secrets Manager<br>GCP service-account key", "secrets_manager", SEC, 1200, 850)

# ---------------- GCP ----------------
gcp("cloudrun", "Cloud Run<br>YOLO / MegaDetector<br>inference API", "cloud_run", 1467, 700, top=True)
gcp("gcs", "Cloud Storage<br>model weights +<br>config.json (version)", "cloud_storage", 1627, 700, top=True)

# ---------------- Edges ----------------
edge("e1", "user", "s3web", "①", points=[(70, 100)], extra="exitX=0.5;exitY=0;entryX=0;entryY=0.5;")
edge("e2", "user", "cognito", "②", both=True, points=[(110, 482), (110, 280)],
     extra="exitX=1;exitY=0.2;entryX=0;entryY=0.5;")
edge("e3", "user", "apigw", "③", points=[(140, 512), (140, 460)], extra="exitX=1;exitY=0.7;entryX=0;entryY=0.5;")
edge("e3a", "apigw", "cognito", "", dashed=True, points=[(340, 445), (340, 280)], extra="exitX=1;exitY=0.25;entryX=1;entryY=0.5;endArrow=open;")
edge("e3b", "apigw", "lambdaGrp", "", extra="exitX=1;exitY=0.5;entryX=0;entryY=0.75;")
edge("e3c", "lambdaGrp", "ddb", "", both=True, extra="exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
edge("e4", "user", "s3media", "④", points=[(85, 790)], extra="exitX=0.75;exitY=1;entryX=0;entryY=0.5;")
edge("eDel", "lDelete", "s3media", "", points=[(440, 434), (440, 700), (520, 700)],
     extra="exitX=0;exitY=0.5;entryX=0.5;entryY=0;")
edge("eChat", "lChat", "lex", "", extra="exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
edge("eFulfil", "lex", "lSearch", "", dashed=True, points=[(750, 209)],
     extra="exitX=0.5;exitY=0;entryX=1;entryY=0.5;")
edge("e5", "s3media", "lThumb", "⑤", dashed=True, both=True, points=[(600, 884)],
     extra="exitX=1;exitY=0.85;entryX=0;entryY=0.5;")
edge("e6", "s3media", "lTag", "⑥", dashed=True, points=[(700, 775), (700, 724)],
     extra="exitX=1;exitY=0.25;entryX=0;entryY=0.5;")
edge("e7", "lTag", "cloudrun", "⑦", extra="exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
edge("e7a", "lTag", "secrets", "", dashed=True, points=[(1110, 741), (1110, 880)],
     extra="exitX=1;exitY=0.85;entryX=0;entryY=0.5;endArrow=open;")
edge("e8", "cloudrun", "gcs", "⑧", extra="exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
edge("e9", "lTag", "ddb", "⑨", points=[(1030, 348)], extra="exitX=0.5;exitY=0;entryX=1;entryY=0.8;")
edge("e10", "ddb", "lNotify", "⑩", dashed=True, extra="exitX=1;exitY=0.3;entryX=0;entryY=0.25;")
edge("e10a", "lNotify", "sns", "", extra="exitX=1;exitY=0.25;entryX=0;entryY=0.3;")
edge("e10b", "sns", "email", "", extra="exitX=1;exitY=0.3;entryX=0;entryY=0.25;")
edge("e11", "user", "cloudrun", "⑪", points=[(55, 1070), (1500, 1070)],
     extra="exitX=0.25;exitY=1;entryX=0.5;entryY=1;strokeColor=#1A73E8;")

# ---------------- Legend ----------------
legend = (
    "<b style='font-size:14px'>Flow legend</b><br>"
    "① Load the web UI from S3 static hosting<br>"
    "② Sign up / sign in via Cognito Hosted UI &rarr; ID token (JWT)<br>"
    "③ REST calls with JWT; API Gateway validates it against the Cognito User Pool, then invokes the API Lambdas<br>"
    "④ Browser uploads the file straight to S3 with a presigned PUT URL (duplicate SHA-256 checksums rejected first)<br>"
    "⑤ S3 ObjectCreated &rarr; thumbnail Lambda writes a resized, compressed copy to thumbnails/<br>"
    "⑥ S3 ObjectCreated &rarr; tagging Lambda (videos: one frame every 10 s)<br>"
    "⑦ Tagging Lambda calls Cloud Run over HTTPS with a GCP ID token and a presigned GET URL (AWS &rarr; GCP only, no AWS keys in GCP)<br>"
    "⑧ Cloud Run loads the model version named in config.json &rarr; new model with no code change<br>"
    "⑨ Species counts, file type, file URL and thumbnail URL saved to DynamoDB<br>"
    "⑩ DynamoDB Streams &rarr; notify Lambda &rarr; SNS (filter policy per tag) &rarr; subscriber email<br>"
    "⑪ Query by file / lost-pet photo: browser sends image + Cognito JWT to Cloud Run, which verifies it with the "
    "Cognito JWKS and runs inference in memory (never stored); the returned tags are then searched via ③<br>"
    "Chatbot: chat Lambda &rarr; Lex V2 &rarr; fulfillment by the search Lambda<br>"
    "<br><b>Line styles:</b> solid = synchronous request &nbsp;&nbsp; dashed = asynchronous event / token validation"
)
vertex("legend", legend,
       "rounded=1;arcSize=2;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacing=12;fontSize=12;"
       "fillColor=#F8F9FA;strokeColor=#AAB7B8;fontColor=#232F3E;",
       170, 1110, 1570, 245)

xml = ('<mxfile host="app.diagrams.net"><diagram id="arch" name="Architecture">'
       '<mxGraphModel dx="1800" dy="1400" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
       'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1800" pageHeight="1450" math="0" shadow="0">'
       '<root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) +
       '</root></mxGraphModel></diagram></mxfile>')

out = r"C:\Users\12462\Desktop\note\fit3184\A2\architecture.drawio"
with open(out, "w", encoding="utf-8") as f:
    f.write(xml)
print("wrote", out)
