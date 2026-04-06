import os
import stat
import argparse
import pwd
import grp
from pathlib import Path
import time

def permisos_legibles(mode):
    permisos = ""

    permisos += "d" if stat.S_ISDIR(mode) else "-"
    permisos += "l" if stat.S_ISLNK(mode) else "-"

    permisos_str = ""
    permisos_str += "r" if mode & stat.S_IRUSR else "-"
    permisos_str += "w" if mode & stat.S_IWUSR else "-"
    permisos_str += "x" if mode & stat.S_IXUSR else "-"
    permisos_str += "r" if mode & stat.S_IRGRP else "-"
    permisos_str += "w" if mode & stat.S_IWGRP else "-"
    permisos_str += "x" if mode & stat.S_IXGRP else "-"
    permisos_str += "r" if mode & stat.S_IROTH else "-"
    permisos_str += "w" if mode & stat.S_IWOTH else "-"
    permisos_str += "x" if mode & stat.S_IXOTH else "-"

    return permisos_str

parser = argparse.ArgumentParser(description="Inspector de archivos")
parser.add_argument("ruta", help="Archivo o directorio")
args = parser.parse_args()

path = Path(args.ruta)

if not path.exists():
    print("Error: El archivo no existe")
    exit(1)

try:
    info = path.lstat()  # importante para symlinks

    print(f"Archivo: {path}")

    # Tipo
    if path.is_symlink():
        destino = os.readlink(path)
        print(f"Tipo: enlace simbólico -> {destino}")
    elif path.is_dir():
        print("Tipo: directorio")
    elif stat.S_ISCHR(info.st_mode):
        print("Tipo: dispositivo de caracteres")
    else:
        print("Tipo: archivo regular")

    # Tamaño
    tamaño = info.st_size
    print(f"Tamaño: {tamaño} bytes")

    # Permisos
    permisos = permisos_legibles(info.st_mode)
    octal = oct(info.st_mode & 0o777)
    print(f"Permisos: {permisos} ({octal[-3:]})")

    # Usuario y grupo
    try:
        usuario = pwd.getpwuid(info.st_uid).pw_name
    except:
        usuario = info.st_uid

    try:
        grupo = grp.getgrgid(info.st_gid).gr_name
    except:
        grupo = info.st_gid

    print(f"Propietario: {usuario} (uid: {info.st_uid})")
    print(f"Grupo: {grupo} (gid: {info.st_gid})")

    print(f"Inodo: {info.st_ino}")
    print(f"Enlaces duros: {info.st_nlink}")

    # Fechas
    print(f"Última modificación: {time.ctime(info.st_mtime)}")
    print(f"Último acceso: {time.ctime(info.st_atime)}")

    # Directorio
    if path.is_dir():
        try:
            contenido = list(path.iterdir())
            print(f"Contenido: {len(contenido)} elementos")
        except PermissionError:
            print("Contenido: no se puede acceder (permiso denegado)")

except Exception as e:
    print(f"Error: {e}")
    exit(1)