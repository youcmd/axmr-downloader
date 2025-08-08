"获取音声作品（RJ号）的元数据及文件清单，并下载到指定目录。支持自定义镜像站点和代理设置。"

# SPDX-FileCopyrightText: 2025 thiliapr <thiliapr@tutanota.com>
# SPDX-FileContributor: thiliapr <thiliapr@tutanota.com>
# SPDX-License-Identifier: AGPL-3.0-or-later

import argparse
from functools import partial
from io import StringIO
import pathlib
import subprocess
import shutil
import tempfile
from typing import Optional
import orjson
import re


def request_by_curl(
    url: str,
    curl_path: pathlib.Path,
    doh_url: Optional[str] = None,
    save_to_file: Optional[pathlib.Path] = None,
    show_progress: bool = False,
    proxy: Optional[str] = None,
    timeout: Optional[float] = None,
    args: Optional[list[str]] = None
) -> bytes:
    """
    使用 curl 命令获取指定 URL 的内容。

    Args:
        url: 要获取的 URL。
        curl_path: curl 命令路径，如果为 None，则使用系统默认的 curl。
        doh_url: DoH URL，用于 DNS over HTTPS 查询。
        save_to_file: 如果指定，则将内容保存到该文件路径，否则输出到标准输出。
        show_progress: 是否显示下载进度条。
        proxy: 代理地址，格式为 {protocol}://{host}:{port}。
        timeout: 超时时间，单位为秒。
        args: 可选的其他 curl 参数。

    Returns:
        获取到的内容字节串。
    """
    # 构建 curl 命令
    cmd = [str(curl_path), "--http3", url]

    # 如果指定了 DoH URL，则添加 `--doh-url` 参数。
    if doh_url:
        cmd.extend(["--doh-url", doh_url])

    # 如果指定了保存路径，则添加 `-o` 参数；否则，使用 `-o -` 将输出重定向到标准输出，并添加 `--continue-at -` 以支持断点续传。
    if save_to_file is not None:
        cmd.extend(["--output", str(save_to_file), "--continue-at", "-"])
    else:
        cmd.extend(["--output", "-"])  # 输出到标准输出

    # 添加进度条或静默模式
    if show_progress:
        cmd.append("--progress-bar")
    else:
        cmd.append("--silent")

    # 添加代理设置
    if proxy:
        cmd.extend(["--proxy", proxy])

    # 添加超时时间设置
    if timeout:
        cmd.extend(["--connect-timeout", str(timeout)])

    # 如果提供了额外的 curl 参数，则添加到命令中
    if args:
        cmd.extend(args)
    
    print(cmd)

    # 执行 curl 命令并返回输出
    return subprocess.check_output(cmd)

def aria2c(
    url: str,
    doh_url: Optional[str] = None,
    save_to_file: Optional[pathlib.Path] = None,
    show_progress: bool = False,
    proxy: Optional[str] = None,
    timeout: Optional[float] = None,
    args: Optional[list[str]] = None
) -> bytes:

    aria2c_path = shutil.which("aria2c")
    
    if not aria2c_path:
        raise FileNotFoundError("aria2c not found in system PATH")
    
    # download_dir = "/tmp"
    # temp_save_to_file = str(save_to_file).replace("/tmp/", "")
    temp_save_to_file = str(save_to_file)
    
    cmd = [aria2c_path, "-x 8","--auto-file-renaming=false"]

    # If DoH URL is specified
    if doh_url:
        cmd.extend(["--doh-server="+doh_url])

    # If save path is provided
    if save_to_file is not None:
        # cmd.extend(["--out="+temp_save_to_file, "--continue=true"])
        cmd.extend(["--out="+temp_save_to_file, "-c"])
    else:
        # Aria2c cannot stream to stdout directly like curl
        raise ValueError("aria2c does not support stdout output (-o -). Please provide a file path.")

    # Progress display settings
    if show_progress:
        cmd.append("--summary-interval=1")  # Frequent progress updates
    else:
        cmd.append("--quiet=true")  # Suppress all output

    # Proxy settings
    if proxy:
        cmd.extend(["--all-proxy="+proxy])

    # Timeout setting
    if timeout:
        cmd.extend(["--connect-timeout="+str(timeout)])

    # Add additional args
    if args:
        cmd.extend(args)

    cmd.append(url)

    print(cmd)

    return subprocess.check_output(cmd)


def convert_directory_to_files(directory: dict, current_path: pathlib.PurePath = pathlib.PurePath(".")) -> list[tuple[pathlib.PurePath, dict]]:
    """
    将目录结构转换为文件列表。

    Args:
        directory: 目录结构字典，包含子目录和文件信息。
        current_path: 当前路径，用于递归构建完整路径。

    Returns:
        包含所有文件的完整路径列表。
    """
    # 初始化文件列表
    files = []

    # 遍历目录中的子项
    for item in directory.get("children", []):
        # 如果子项是文件夹，则递归调用函数获取其下的文件
        if item["type"] == "folder":
            files.extend(convert_directory_to_files(item, current_path / item["title"]))
        # 如果子项是文件，则将其完整路径添加到文件列表
        else:
            files.append((current_path / item["title"], item))

    # 返回所有文件的完整路径列表
    return files


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """
    解析命令行参数。

    Args:
        args: 可选的命令行参数列表。如果为 None，则使用 sys.argv[1:]。

    Returns:
        解析后的命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="从 asmr.one 获取音声作品的元数据和文件清单，并下载到指定目录。")
    parser.add_argument("rj_id", type=str, help="音声的 RJ 号。例如网址为 https://www.dlsite.com/maniax/work/=/product_id/RJ285384.html，则 RJ 号为 285384")
    parser.add_argument("-e", "--endpoint", type=str, default="https://api.asmr-200.com", help="下载的镜像站点，默认为 %(default)s")
    parser.add_argument("-o", "--output-path", type=pathlib.Path, help="音声保存路径，默认为: 当前目录 / work / '{TITLE} [{RJ_ID}] [{circle_name}]'，其中 TITLE 为音声标题，RJ_ID 为音声 RJ 号，circle_name 为社团名称")
    parser.add_argument("-d", "--doh-url", type=str, default="https://v.recipes/dns-query", help="DoH URL，默认为 %(default)s")
    parser.add_argument("-c", "--curl-path", type=pathlib.Path, help="curl 命令的路径，如果未指定则使用系统默认的 curl")
    parser.add_argument("-i", "--editor-path", type=pathlib.Path, help="编辑器的路径。如果未指定，则尝试使用系统默认的编辑器（notepad 或 gedit）")
    parser.add_argument("-p", "--proxy", type=str, help="代理地址，格式为 {protocol}://{host}:{port}，例如 socks5h://127.0.1:1080（socks5h的`h`表示解析域名时使用代理，不建议省略）")
    parser.add_argument("-t", "--timeout", type=int, default=10, help="请求超时时间，单位为秒，默认为 %(default)s 秒")
    parser.add_argument("-v", "--detail", action="store_true", help="在询问要下载的文件时，显示文件大小和音频时长等详细信息")
    return parser.parse_args(args)

def extract_trailing_number(code: str) -> int:
    match = re.search(r"(\d+)$", code)
    if not match:
        raise ValueError(f"No trailing digits found in {code!r}")
    return int(match.group(1))

def main(args: argparse.Namespace):
    # 如果未指定 curl_path，则尝试使用 shutil.which 查找系统中的 curl 命令。
    curl_path = args.curl_path
    if curl_path is None:
        curl_path = shutil.which("curl")

        # 如果未找到 curl 命令，则抛出异常。
        if curl_path is None:
            raise FileNotFoundError("未找到 curl 命令，请安装 curl 或指定 --curl-path 参数。")

        # 确保 curl_path 是 pathlib.Path 对象
        curl_path = pathlib.Path(curl_path)
    # 如果指定的 curl_path 不存在或不是一个文件，则抛出异常。
    elif not curl_path.exists() or not curl_path.is_file():
        raise FileNotFoundError(f"`{curl_path}` 不存在或不是一个文件。请检查路径是否正确。")

    # 使用 partial 函数预设参数，方便后续调用
    fast_curl = partial(request_by_curl, curl_path=curl_path, doh_url=args.doh_url, proxy=args.proxy, timeout=args.timeout)

    rj_id = extract_trailing_number(args.rj_id)
    
    # 获取音声信息
    work_info = orjson.loads(fast_curl(f"{args.endpoint}/api/workInfo/{rj_id}"))

    # 打印音声信息
    print("音声信息:")
    for key, value in work_info.items():
        if isinstance(value, str):
            print(f"{key}: {value}")
    print()


    # 设置输出路径
    output_path = args.output_path or pathlib.Path.cwd() / f"work/{work_info['title']} [{work_info['id']}] [{work_info['name']}]"

    # 获取音声目录结构
    directory = {
        "type": "folder",
        "children": orjson.loads(fast_curl(f"{args.endpoint}/api/tracks/{rj_id}?v=2"))
    }

    # aria2c(f"{args.endpoint}/api/cover/{rj_id}.jpg?type=main",save_to_file=f"{output_path}/cover.jpg")
    
    # 将目录结构转换为文件列表
    files = convert_directory_to_files(directory)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # 将文件列表写入临时文件，然后打开notepad编辑，以询问哪个文件需要下载（像git commit一样）
        temp_file_path = pathlib.Path(temp_dir) / f"asmr_one_download_{args.rj_id}.txt"

        # 使用 StringIO 来构建文件内容
        content = StringIO()
        content.write("# 每一行代表一个文件。注释行以 # 开头，不用管它们。\n")
        content.write("# 请删除不想下载的文件，然后保存并关闭编辑器。\n\n")

        for path, info in files:
            if args.detail:
                # 写入文件信息
                content.write(f"# size={info['size'] / 1024 ** 2:.3f} MiB")

                # 如果是音频文件，添加时长信息
                if info["type"] == "audio":
                    duration = info['duration']
                    content.write(f", duration={int(duration // 60)}min{duration % 60:.2f}sec")

                # 添加换行
                content.write("\n")

            # 写入文件路径
            content.write(f"{path}\n")

        # 创建临时文件并写入内容
        with open(temp_file_path, "w", encoding="utf-8") as temp_file:
            temp_file.write(content.getvalue())

        # 读取用户选择的文件列表
        with open(temp_file_path, "r", encoding="utf-8") as temp_file:
            # 这里没有用 not line.startswith("#") 来过滤注释行，是因为获取要下载的文件的逻辑是 set(user_selected_files) & set(files)
            # set(user_selected_files) 表示用户想下载的文件列表，set(files) 表示有效的文件列表。
            # 即使用户选择了无效的文件，因为交集操作会过滤掉无效的文件，所以最终下载的文件列表中不会包含无效的文件。
            # `#`开头的行是注释行，可以看作是无效文件，但不影响正常下载。
            # 而且如果有些文件以`#`开头，按照 not line.startswith("#") 过滤掉的话，用户就无法下载这些文件了。
            user_selected_files = {line.strip() for line in temp_file if line.strip()}

    # 筛选出用户选择的文件
    selected_files = [(path, info) for path, info in files if str(path) in user_selected_files]

    # 打印用户选择的文件路径
    print("将要下载的文件:")
    for file_path, _ in selected_files:
        print(file_path)
    print()

    with tempfile.TemporaryDirectory() as download_temp_dir:
        download_temp_dir = pathlib.Path(download_temp_dir)

        # 下载用户选择的文件
        for file_path, file_info in selected_files:
            # 确保输出路径的父目录存在
            file_output_path = output_path / file_path
            file_output_path.parent.mkdir(parents=True, exist_ok=True)

            # 复制文件到临时目录
            # 使用哈希值作为文件名，避免 curl 下载时的路径问题
            download_temp_file = download_temp_dir / file_info["hash"].replace("/", "_")
            if file_output_path.exists():
                if file_output_path.stat().st_size >= file_info["size"]:
                    continue
                shutil.copy(file_output_path, download_temp_file)
            # else:
            #     download_temp_file.write_bytes(b"")  # 确保文件存在

            # 打印正在下载的文件
            print(file_path)

            # 获取文件下载链接
            url = file_info["mediaDownloadUrl"]

            # 获取下载链接状态码，如果状态码不是 200，则尝试 mediaStreamUrl 作为备用下载链接
            response_header = fast_curl(url, args=["--head"])
            if int(response_header.splitlines()[0].split()[1]) != 200:
                # 尝试 mediaStreamUrl 作为备用下载链接
                url = file_info["mediaStreamUrl"]

                # 如果 mediaStreamUrl 也不可用，则跳过下载
                response_header = fast_curl(url, args=["--head"])
                status_code = int(response_header.splitlines()[0].split()[1])
                if status_code != 200:
                    continue
            
            # 下载到临时文件
            try:
                try:
                    aria2c(url, save_to_file=file_output_path, show_progress=False)
                    # shutil.move(download_temp_file, file_output_path)
                except subprocess.CalledProcessError:
                    # 如果下载失败，可能是网络问题或链接失效，重试下载
                    pass
            except KeyboardInterrupt:
                break
            finally:
                # 将下载的文件移动到输出路径
                # shutil.move(download_temp_file, file_output_path)
                print(file_output_path)
    aria2c(f"{args.endpoint}/api/cover/{rj_id}.jpg?type=main",save_to_file=f"{output_path}/cover.jpg")

if __name__ == "__main__":
    main(parse_args())
