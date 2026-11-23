"""One-day test certificates for the loopback demonstration."""
import shutil
import subprocess


def issue_certificates(base, openssl):
    def issue(*args):
        subprocess.run([openssl,*args],cwd=base,check=True,capture_output=True)

    issue('req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=Test CA',
          '-addext','basicConstraints=critical,CA:TRUE','-keyout','ca.key','-out','ca.pem')
    for name in ('A','B','directory'):
        root = base/name
        root.mkdir()
        shutil.copyfile(base/'ca.pem',root/'ca.pem')
        issue('req','-new','-newkey','rsa:2048','-nodes','-subj','/CN='+name,
              '-keyout',str(root/'node.key'),'-out','node.csr')
        (base/'extensions').write_text('subjectAltName=IP:127.0.0.1,DNS:localhost,URI:urn:roundtable:peer:'+name+
            '\nextendedKeyUsage=serverAuth,clientAuth\nbasicConstraints=CA:FALSE\n')
        issue('x509','-req','-in','node.csr','-CA','ca.pem','-CAkey','ca.key','-CAcreateserial',
              '-days','1','-extfile','extensions','-out',str(root/'node.pem'))
