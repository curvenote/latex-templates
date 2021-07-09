from os import path, mkdir, environ
import pathlib
import json
import typer
import yaml
import tempfile
from analyse import analyse
from shutil import make_archive, move
from TemplateStorage import TemplateStorage
from TemplateAssets import TemplateAssets
import subprocess
from google.cloud import storage as gcp_storage
from typing import Dict, List


import logging
logging.getLogger().setLevel(logging.INFO)

def get_local_options(latex_path: str, tmpl: str):
  with open(path.join(latex_path, tmpl, 'template.yml')) as oyml:
    return yaml.load(oyml, Loader=yaml.FullLoader)

def main(repo_path: str):
  logging.info(f"repo_path set to {repo_path}")
  if not path.exists(repo_path):
    raise IOError(f"Repo not found at {repo_path}")

  latex_path = path.join(repo_path, 'latex')
  logging.info(f"Looking for latex template in {latex_path}")

  with tempfile.TemporaryDirectory() as tmp_folder:
    logging.info("Created Temporary Folder")

    # EARLY CHECK for bucket permissions
    try:
      storage  = TemplateStorage(
        gcp_storage.Client(environ["GCP_PROJECT_ID"]),
        environ['BUCKET_NAME'],
        tmp_folder
        )
    except Exception as err:
      logging.error("Failed to get bucket, maybe an auth issue")
      raise err

    # pull the previous listing down
    prev_listing = storage.get_listing()
    # Analyse the repo contents, listing and diff since last processing pass
    all_templates, to_process, to_remove_from_bucket = analyse(latex_path, prev_listing)

    # process assets ready for update
    logging.info("Start processing...")
    processed_assets: List[TemplateAssets] = []
    for tmpl in to_process:
    # run procesing steps on each template; zip
      logging.info(f"processing: {tmpl}")
      mkdir(path.join(tmp_folder, tmpl))

      has_original = False
      if path.exists(path.join(latex_path, tmpl, 'original')):
        has_original = True
        logging.info(f"Found original directory for {tmpl}, moving")
        move(path.join(latex_path, tmpl, 'original'), path.join(tmp_folder, 'original'))

      zip_filepath = make_archive(
        path.join(tmp_folder, tmpl, 'latex.template'), 'zip', path.join(latex_path, tmpl))
      logging.info(f"created zipfile {zip_filepath}")

      if has_original:
        logging.info(f"Moving original back to template folder")
        move(path.join(tmp_folder, 'original'), path.join(latex_path, tmpl, 'original'))

      options_json_filepath = path.join(tmp_folder, tmpl, 'options.json')
      options = get_local_options(latex_path, tmpl)
      with open(options_json_filepath, 'w') as ojson:
        json.dump(options, ojson, indent=4)
      logging.info(f"created options.json {zip_filepath}")

      processed_assets.append(TemplateAssets(tmpl, zip_filepath, options_json_filepath))
    logging.info("Processing complete")

    # push new templates
    if len(processed_assets):
      logging.info("Start uploading processed assets...")
      for assets in processed_assets:
        storage.push_template_asset(assets)
        logging.info(f"{assets.name} uploaded")
      logging.info("Upload complete")

    if len(to_remove_from_bucket):
      logging.info("Removing deleted templates...")
      # delete removed templates
      for tmpl in to_remove_from_bucket:
        logging.info(f"Removing {tmpl}")
        storage.delete_template_asset(tmpl)
      logging.info("Removal complete")

    # TODO: commit to git - if needed

    # get current git sha for tagging
    gitsha = subprocess.check_output(
      'git rev-parse --short HEAD', shell=True, encoding="utf-8").strip()

    # update listings and refresh metadata
    all = []
    for tmpl in all_templates:
      options = get_local_options(latex_path, tmpl)
      all.append(dict(id=tmpl, commit=gitsha, **options['metadata']))

    # update listings and lastrun commit hash
    logging.info(f"Logging this run with current git sha {gitsha}")
    storage.push_listing({
      "all": all,
      "lastrun": { "commit": gitsha }
    })

    # TODO: git push - id needed

if __name__ == "__main__":
  logging.info("Started Python Processing Script")

  missing_env = []
  if 'GOOGLE_APPLICATION_CREDENTIALS' not in environ:
    typer.echo('GOOGLE_APPLICATION_CREDENTIALS missing, run gcloud-setup action prior to this')
    typer.Exit(1)
  if 'GCP_PROJECT_ID' not in environ:
    missing_env.append("GCP_PROJECT_ID")
  if 'GCP_SA_KEY' not in environ:
    missing_env.append("GCP_SA_KEY")
  if 'BUCKET_NAME' not in environ:
    missing_env.append("BUCKET_NAME")
  if len(missing_env) > 1:
    typer.echo(f"{','.join(missing_env)} not set")
    raise typer.Exit(1)

  typer.run(main)
