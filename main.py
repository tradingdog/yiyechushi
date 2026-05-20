from image_generator import generate_images_from_prompt_file


def main() -> None:
    print("开始生成图片，请等待接口返回结果...")

    try:
        result = generate_images_from_prompt_file(
            dish_name="冬阴功蹄花虾汤",
            prompt_file_name="临时调试prompt.txt",
        )
    except Exception as exc:
        print(f"运行失败：{exc}")
        raise SystemExit(1) from exc

    print("图片生成完成")
    print(f"输出目录：{result['output_dir']}")
    print(f"本次实际 prompt：{result['rendered_prompt_file']}")
    for saved_file in result["saved_files"]:
        print(f"已保存：{saved_file}")


if __name__ == "__main__":
    main()
