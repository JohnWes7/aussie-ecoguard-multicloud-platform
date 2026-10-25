// Finds the species in an image without storing it, for the queries that start
// from a file (find files with the same tags; find a lost pet by its photo).
// The image goes straight to Cloud Run POST /detect with the user's ID token;
// it is never uploaded to S3 (Spec 4.3). Needs auth.js (getIdToken).
//
//   try {
//     const { tags, attributes } = await detectFile(file);  // {"cat": 1}, ["orange"]
//     // ...search with them
//   } catch (err) {
//     showToast(err.message, "error");
//   }
//
// The first call after a quiet period can take about a minute: Cloud Run starts
// an instance and loads the models first. Show the user that it is working.

const DETECT_TYPES = ["image/jpeg", "image/png"];
const DETECT_MAX_BYTES = 30 * 1024 * 1024; // the same limit as the service

async function detectFile(file) {
  // Checked here too, so a wrong file is refused at once instead of after the upload.
  if (!DETECT_TYPES.includes(file.type)) throw new Error("Choose a JPEG or PNG image");
  if (file.size > DETECT_MAX_BYTES) throw new Error("The image is larger than 30 MB");

  const response = await fetch(`${window.CONFIG.inferenceUrl}/detect`, {
    method: "POST",
    headers: { Authorization: `Bearer ${await getIdToken()}`, "Content-Type": file.type },
    body: file, // the image itself, not JSON
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `detect returned ${response.status}`);
  return body; // {tags, attributes, model}
}
